from __future__ import annotations

import json
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


REGION = "us-east-2"
STATE_FILE = Path(__file__).with_name("estado_redshift.json")


def cargar() -> dict:
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def codigo(error: ClientError) -> str:
    return error.response.get("Error", {}).get("Code", "")


def esperar_eliminacion(cliente, metodo: str, parametro: str, nombre: str) -> None:
    funcion = getattr(cliente, metodo)
    for _ in range(120):
        try:
            funcion(**{parametro: nombre})
            time.sleep(10)
        except ClientError as error:
            if codigo(error) == "ResourceNotFoundException":
                return
            raise
    raise TimeoutError(f"{nombre} no se eliminó dentro del tiempo esperado")


def vaciar_bucket(s3, bucket: str) -> None:
    paginator = s3.get_paginator("list_object_versions")
    for pagina in paginator.paginate(Bucket=bucket):
        objetos = [
            {"Key": item["Key"], "VersionId": item["VersionId"]}
            for item in pagina.get("Versions", []) + pagina.get("DeleteMarkers", [])
        ]
        if objetos:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": objetos})


def main() -> None:
    estado = cargar()
    if not estado:
        print("No quedaron recursos registrados por este programa.")
        return

    region = estado.get("region", REGION)
    redshift = boto3.client("redshift-serverless", region_name=region)
    secrets = boto3.client("secretsmanager", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    iam = boto3.client("iam", region_name=region)
    errores: list[str] = []

    print("[1/5] Eliminando el workgroup de Redshift Serverless...")
    workgroup = estado.get("workgroup_name")
    if workgroup:
        try:
            try:
                redshift.delete_workgroup(workgroupName=workgroup)
            except ClientError as error:
                if codigo(error) != "ResourceNotFoundException":
                    raise
            esperar_eliminacion(redshift, "get_workgroup", "workgroupName", workgroup)
        except (ClientError, TimeoutError) as error:
            errores.append(f"Workgroup: {error}")

    print("[2/5] Eliminando el namespace y su secreto administrado...")
    namespace = estado.get("namespace_name")
    if namespace:
        try:
            try:
                redshift.delete_namespace(namespaceName=namespace)
            except ClientError as error:
                if codigo(error) != "ResourceNotFoundException":
                    raise
            esperar_eliminacion(redshift, "get_namespace", "namespaceName", namespace)
        except (ClientError, TimeoutError) as error:
            errores.append(f"Namespace: {error}")

    secret_arn = estado.get("secret_arn")
    if secret_arn:
        try:
            secrets.describe_secret(SecretId=secret_arn)
            try:
                secrets.delete_secret(SecretId=secret_arn, ForceDeleteWithoutRecovery=True)
            except ClientError as error:
                if codigo(error) not in {"ResourceNotFoundException", "InvalidRequestException"}:
                    raise
        except ClientError as error:
            if codigo(error) != "ResourceNotFoundException":
                errores.append(f"Secreto: {error}")

    print("[3/5] Vaciando y eliminando el bucket S3...")
    bucket = estado.get("bucket_name")
    if bucket:
        try:
            vaciar_bucket(s3, bucket)
            s3.delete_bucket(Bucket=bucket)
        except ClientError as error:
            if codigo(error) not in {"NoSuchBucket", "NoSuchBucketException"}:
                errores.append(f"Bucket: {error}")

    print("[4/5] Eliminando la política del rol IAM...")
    role_name = estado.get("role_name")
    if role_name:
        try:
            iam.delete_role_policy(RoleName=role_name, PolicyName="LeerCsvActividadPunto5")
        except ClientError as error:
            if codigo(error) != "NoSuchEntity":
                errores.append(f"Política IAM: {error}")

    print("[5/5] Eliminando el rol IAM...")
    if role_name:
        try:
            iam.delete_role(RoleName=role_name)
        except ClientError as error:
            if codigo(error) != "NoSuchEntity":
                errores.append(f"Rol IAM: {error}")

    if errores:
        print("\nLa eliminación quedó incompleta:")
        for error in errores:
            print(f"- {error}")
        print(f"Se conserva {STATE_FILE} para volver a intentarlo.")
        raise SystemExit(1)

    STATE_FILE.unlink(missing_ok=True)
    print("\nELIMINACIÓN COMPLETADA")
    print("No quedaron recursos de Redshift, S3, IAM o Secrets Manager registrados.")


if __name__ == "__main__":
    main()
