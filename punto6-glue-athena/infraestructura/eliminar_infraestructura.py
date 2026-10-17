from __future__ import annotations

import json
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


STATE_FILE = Path(__file__).with_name("estado_glue_athena.json")
REGION = "us-east-2"


def cargar() -> dict:
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def codigo(error: ClientError) -> str:
    return error.response.get("Error", {}).get("Code", "")


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
    glue = boto3.client("glue", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    iam = boto3.client("iam", region_name=region)
    errores: list[str] = []

    print("[1/5] Deteniendo y eliminando el crawler...")
    crawler = estado.get("crawler_name")
    if crawler:
        try:
            try:
                actual = glue.get_crawler(Name=crawler)["Crawler"]
                if actual["State"] != "READY":
                    glue.stop_crawler(Name=crawler)
                    while glue.get_crawler(Name=crawler)["Crawler"]["State"] != "READY":
                        time.sleep(5)
                glue.delete_crawler(Name=crawler)
            except ClientError as error:
                if codigo(error) != "EntityNotFoundException":
                    raise
        except ClientError as error:
            errores.append(f"Crawler: {error}")

    print("[2/5] Eliminando tabla y base de datos de Glue...")
    database = estado.get("database_name")
    table = estado.get("table_name")
    if database and table:
        try:
            glue.delete_table(DatabaseName=database, Name=table)
        except ClientError as error:
            if codigo(error) != "EntityNotFoundException":
                errores.append(f"Tabla Glue: {error}")
    if database:
        try:
            glue.delete_database(Name=database)
        except ClientError as error:
            if codigo(error) != "EntityNotFoundException":
                errores.append(f"Base Glue: {error}")

    print("[3/5] Eliminando datos y resultados de Athena del bucket...")
    bucket = estado.get("bucket_name")
    if bucket:
        try:
            vaciar_bucket(s3, bucket)
            s3.delete_bucket(Bucket=bucket)
        except ClientError as error:
            if codigo(error) not in {"NoSuchBucket", "NoSuchBucketException"}:
                errores.append(f"Bucket: {error}")

    role_name = estado.get("role_name")
    print("[4/5] Eliminando permisos del rol Glue...")
    if role_name:
        try:
            iam.delete_role_policy(
                RoleName=role_name, PolicyName="AccesoBucketActividadPunto6"
            )
        except ClientError as error:
            if codigo(error) != "NoSuchEntity":
                errores.append(f"Política inline: {error}")
        try:
            iam.detach_role_policy(
                RoleName=role_name,
                PolicyArn="arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole",
            )
        except ClientError as error:
            if codigo(error) != "NoSuchEntity":
                errores.append(f"Política administrada: {error}")

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
    print("No quedaron recursos de Glue, Athena, S3 o IAM registrados.")


if __name__ == "__main__":
    main()
