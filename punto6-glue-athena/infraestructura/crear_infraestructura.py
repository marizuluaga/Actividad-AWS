from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


REGION = "us-east-2"
BASE_DIR = Path(__file__).resolve().parents[1]
STATE_FILE = Path(__file__).with_name("estado_glue_athena.json")
DATA_FILE = BASE_DIR / "datos" / "ventas.csv"


def guardar(estado: dict) -> None:
    STATE_FILE.write_text(json.dumps(estado, indent=2), encoding="utf-8")


def esperar_crawler(glue, nombre: str) -> dict:
    time.sleep(5)
    for _ in range(120):
        crawler = glue.get_crawler(Name=nombre)["Crawler"]
        estado = crawler["State"]
        ultimo = crawler.get("LastCrawl", {}).get("Status")
        print(f"  Crawler: {estado}; última ejecución: {ultimo or 'pendiente'}")
        if estado == "READY" and ultimo == "SUCCEEDED":
            return crawler
        if estado == "READY" and ultimo in {"FAILED", "CANCELLED"}:
            raise RuntimeError(f"El crawler terminó con estado {ultimo}")
        time.sleep(10)
    raise TimeoutError("El crawler no terminó dentro del tiempo esperado.")


def main() -> None:
    if not DATA_FILE.is_file():
        raise FileNotFoundError(f"No existe {DATA_FILE}")

    sts = boto3.client("sts", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    iam = boto3.client("iam", region_name=REGION)
    glue = boto3.client("glue", region_name=REGION)

    cuenta = sts.get_caller_identity()["Account"]
    sufijo = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    bucket = f"actividad-p6-{cuenta}-{sufijo}"
    base_datos = f"actividad_p6_{sufijo}"
    crawler = f"actividad-p6-{sufijo}-crawler"
    rol = f"actividad-p6-{sufijo}-glue-role"

    estado = {
        "region": REGION,
        "account_id": cuenta,
        "bucket_name": bucket,
        "database_name": base_datos,
        "crawler_name": crawler,
        "role_name": rol,
        "input_prefix": "entrada/",
        "results_prefix": "resultados-athena/",
    }
    guardar(estado)

    confianza = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "glue.amazonaws.com"},
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
            Tagging={"TagSet": [
                {"Key": "Proyecto", "Value": "Actividad AWS"},
                {"Key": "Punto", "Value": "6"},
            ]},
        )

        print("[2/7] Subiendo ventas.csv con 25 registros...")
        s3.upload_file(str(DATA_FILE), bucket, "entrada/ventas.csv")

        print("[3/7] Creando rol IAM para AWS Glue...")
        respuesta = iam.create_role(
            RoleName=rol,
            AssumeRolePolicyDocument=json.dumps(confianza),
            Description="Rol del crawler Glue para la actividad AWS punto 6",
            Tags=[
                {"Key": "Proyecto", "Value": "Actividad AWS"},
                {"Key": "Punto", "Value": "6"},
            ],
        )
        role_arn = respuesta["Role"]["Arn"]
        estado["role_arn"] = role_arn
        guardar(estado)

        iam.attach_role_policy(
            RoleName=rol,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole",
        )
        politica_s3 = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                    "Resource": f"arn:aws:s3:::{bucket}",
                },
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:PutObject"],
                    "Resource": f"arn:aws:s3:::{bucket}/*",
                },
            ],
        }
        iam.put_role_policy(
            RoleName=rol,
            PolicyName="AccesoBucketActividadPunto6",
            PolicyDocument=json.dumps(politica_s3),
        )
        time.sleep(10)

        print("[4/7] Creando la base de datos de Glue...")
        glue.create_database(
            DatabaseInput={
                "Name": base_datos,
                "Description": "Catálogo de ventas de la actividad AWS punto 6",
            },
            Tags={"Proyecto": "Actividad AWS", "Punto": "6"},
        )

        print("[5/7] Creando y ejecutando el crawler...")
        glue.create_crawler(
            Name=crawler,
            Role=role_arn,
            DatabaseName=base_datos,
            Description="Crawler de ventas CSV del punto 6",
            Targets={"S3Targets": [{"Path": f"s3://{bucket}/entrada/"}]},
            SchemaChangePolicy={
                "UpdateBehavior": "UPDATE_IN_DATABASE",
                "DeleteBehavior": "LOG",
            },
            RecrawlPolicy={"RecrawlBehavior": "CRAWL_EVERYTHING"},
            Tags={"Proyecto": "Actividad AWS", "Punto": "6"},
        )
        glue.start_crawler(Name=crawler)
        esperar_crawler(glue, crawler)

        print("[6/7] Consultando la tabla y el esquema inferido...")
        tablas = glue.get_tables(DatabaseName=base_datos)["TableList"]
        if not tablas:
            raise RuntimeError("El crawler no creó ninguna tabla.")
        tabla = tablas[0]
        table_name = tabla["Name"]
        estado["table_name"] = table_name
        guardar(estado)
        print(f"Tabla: {base_datos}.{table_name}")
        for columna in tabla["StorageDescriptor"]["Columns"]:
            print(f"  {columna['Name']}: {columna['Type']}")

        print("[7/7] Infraestructura creada correctamente.")
        print("\nRESUMEN")
        print(f"Bucket: {bucket}")
        print(f"Archivo: s3://{bucket}/entrada/ventas.csv")
        print(f"Rol Glue: {role_arn}")
        print(f"Base Glue: {base_datos}")
        print(f"Crawler: {crawler}")
        print(f"Tabla: {table_name}")
        print(f"Estado guardado en: {STATE_FILE}")

    except (ClientError, RuntimeError, TimeoutError, KeyError) as error:
        guardar(estado)
        print("\nLa creación no terminó.")
        print(f"Error: {error}")
        print("Ejecuta infraestructura/eliminar_infraestructura.py para limpiar.")
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
