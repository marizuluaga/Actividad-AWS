from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


REGION = "us-east-2"
BASE_DIR = Path(__file__).resolve().parents[1]
STATE_FILE = Path(__file__).with_name("estado_redshift.json")
CLIENTES_FILE = BASE_DIR / "datos" / "clientes.csv"
PEDIDOS_FILE = BASE_DIR / "datos" / "pedidos.csv"


def guardar(estado: dict) -> None:
    STATE_FILE.write_text(json.dumps(estado, indent=2), encoding="utf-8")


def esperar_namespace(cliente, nombre: str) -> dict:
    for _ in range(120):
        namespace = cliente.get_namespace(namespaceName=nombre)["namespace"]
        estado = namespace["status"]
        print(f"  Namespace: {estado}")
        if estado == "AVAILABLE":
            return namespace
        if estado in {"MODIFYING", "CREATING"}:
            time.sleep(15)
            continue
        raise RuntimeError(f"Estado inesperado del namespace: {estado}")
    raise TimeoutError("El namespace no quedó disponible dentro del tiempo esperado.")


def esperar_workgroup(cliente, nombre: str) -> dict:
    for _ in range(120):
        workgroup = cliente.get_workgroup(workgroupName=nombre)["workgroup"]
        estado = workgroup["status"]
        print(f"  Workgroup: {estado}")
        if estado == "AVAILABLE":
            return workgroup
        if estado in {"MODIFYING", "CREATING"}:
            time.sleep(15)
            continue
        raise RuntimeError(f"Estado inesperado del workgroup: {estado}")
    raise TimeoutError("El workgroup no quedó disponible dentro del tiempo esperado.")


def main() -> None:
    if not CLIENTES_FILE.is_file() or not PEDIDOS_FILE.is_file():
        raise FileNotFoundError("Faltan datos/clientes.csv o datos/pedidos.csv")

    sts = boto3.client("sts", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    iam = boto3.client("iam", region_name=REGION)
    redshift = boto3.client("redshift-serverless", region_name=REGION)

    account_id = sts.get_caller_identity()["Account"]
    sufijo = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    prefijo = f"actividad-p5-{sufijo}"
    bucket = f"actividad-p5-{account_id}-{sufijo}"
    role_name = f"{prefijo}-copy-role"
    namespace_name = f"{prefijo}-ns"
    workgroup_name = f"{prefijo}-wg"

    estado = {
        "region": REGION,
        "account_id": account_id,
        "prefix": prefijo,
        "bucket_name": bucket,
        "role_name": role_name,
        "namespace_name": namespace_name,
        "workgroup_name": workgroup_name,
        "database": "actividad",
    }
    guardar(estado)

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "redshift.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    try:
        print("[1/7] Creando bucket S3 privado...")
        s3.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
        s3.put_public_access_block(
            Bucket=bucket,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        s3.put_bucket_tagging(
            Bucket=bucket,
            Tagging={
                "TagSet": [
                    {"Key": "Proyecto", "Value": "Actividad AWS"},
                    {"Key": "Punto", "Value": "5"},
                ]
            },
        )

        print("[2/7] Subiendo clientes.csv y pedidos.csv...")
        s3.upload_file(str(CLIENTES_FILE), bucket, "datos/clientes.csv")
        s3.upload_file(str(PEDIDOS_FILE), bucket, "datos/pedidos.csv")

        print("[3/7] Creando el rol IAM para COPY desde S3...")
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Permite a Redshift cargar los CSV del punto 5 desde S3",
            Tags=[
                {"Key": "Proyecto", "Value": "Actividad AWS"},
                {"Key": "Punto", "Value": "5"},
            ],
        )["Role"]
        role_arn = role["Arn"]
        estado["role_arn"] = role_arn
        guardar(estado)

        s3_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                    "Resource": f"arn:aws:s3:::{bucket}",
                },
                {
                    "Effect": "Allow",
                    "Action": "s3:GetObject",
                    "Resource": f"arn:aws:s3:::{bucket}/*",
                },
            ],
        }
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="LeerCsvActividadPunto5",
            PolicyDocument=json.dumps(s3_policy),
        )
        time.sleep(10)

        print("[4/7] Creando namespace con contraseña administrada...")
        redshift.create_namespace(
            namespaceName=namespace_name,
            dbName="actividad",
            adminUsername="administrador",
            manageAdminPassword=True,
            defaultIamRoleArn=role_arn,
            iamRoles=[role_arn],
            tags=[
                {"key": "Proyecto", "value": "Actividad AWS"},
                {"key": "Punto", "value": "5"},
            ],
        )
        namespace = esperar_namespace(redshift, namespace_name)
        secret_arn = namespace["adminPasswordSecretArn"]
        estado["secret_arn"] = secret_arn
        guardar(estado)

        print("[5/7] Creando workgroup Serverless con capacidad mínima...")
        redshift.create_workgroup(
            workgroupName=workgroup_name,
            namespaceName=namespace_name,
            baseCapacity=4,
            publiclyAccessible=False,
            tags=[
                {"key": "Proyecto", "value": "Actividad AWS"},
                {"key": "Punto", "value": "5"},
            ],
        )
        workgroup = esperar_workgroup(redshift, workgroup_name)

        estado["endpoint"] = workgroup.get("endpoint", {}).get("address")
        guardar(estado)

        print("[6/7] Verificando los archivos de S3...")
        for objeto in s3.list_objects_v2(Bucket=bucket).get("Contents", []):
            print(f"  s3://{bucket}/{objeto['Key']} ({objeto['Size']} bytes)")

        print("[7/7] Infraestructura creada correctamente.")
        print("\nRESUMEN")
        print(f"Bucket: {bucket}")
        print(f"Rol COPY: {role_arn}")
        print(f"Namespace: {namespace_name}")
        print(f"Workgroup: {workgroup_name}")
        print(f"Base capacity: {workgroup['baseCapacity']} RPU")
        print(f"Secreto administrado: {secret_arn}")
        print(f"Estado guardado en: {STATE_FILE}")

    except (ClientError, RuntimeError, TimeoutError, KeyError) as error:
        guardar(estado)
        print("\nLa creación no terminó.")
        print(f"Error: {error}")
        print("Ejecuta infraestructura/eliminar_infraestructura.py para limpiar.")
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
