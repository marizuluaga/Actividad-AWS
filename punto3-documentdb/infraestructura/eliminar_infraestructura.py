from __future__ import annotations

import json
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, WaiterError


REGION = "us-east-2"
STATE_FILE = Path(__file__).with_name("estado_documentdb.json")


def cargar_estado() -> dict:
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def codigo(error: ClientError) -> str:
    return error.response.get("Error", {}).get("Code", "")


def borrar_grupo_seguridad(ec2, group_id: str | None) -> None:
    if not group_id:
        return
    for intento in range(12):
        try:
            ec2.delete_security_group(GroupId=group_id)
            return
        except ClientError as error:
            if codigo(error) == "InvalidGroup.NotFound":
                return
            if codigo(error) == "DependencyViolation" and intento < 11:
                time.sleep(10)
                continue
            raise


def main() -> None:
    estado = cargar_estado()
    if not estado:
        print("No quedaron recursos registrados por este programa.")
        return

    ec2 = boto3.client("ec2", region_name=estado.get("region", REGION))
    docdb = boto3.client("docdb", region_name=estado.get("region", REGION))
    secrets = boto3.client("secretsmanager", region_name=estado.get("region", REGION))
    errores: list[str] = []

    print("[1/7] Eliminando la instancia de DocumentDB...")
    instance_id = estado.get("documentdb_instance_id") or estado.get("db_instance_identifier")
    if instance_id:
        try:
            docdb.delete_db_instance(DBInstanceIdentifier=instance_id)
            docdb.get_waiter("db_instance_deleted").wait(
                DBInstanceIdentifier=instance_id,
                WaiterConfig={"Delay": 30, "MaxAttempts": 80},
            )
        except ClientError as error:
            if codigo(error) != "DBInstanceNotFound":
                errores.append(f"Instancia DocumentDB: {error}")
        except WaiterError as error:
            errores.append(f"Espera de instancia DocumentDB: {error}")

    print("[2/7] Eliminando el clúster de DocumentDB...")
    cluster_id = estado.get("documentdb_cluster_id") or estado.get("db_cluster_identifier")
    if cluster_id:
        try:
            docdb.delete_db_cluster(
                DBClusterIdentifier=cluster_id, SkipFinalSnapshot=True
            )
            docdb.get_waiter("db_cluster_deleted").wait(
                DBClusterIdentifier=cluster_id,
                WaiterConfig={"Delay": 30, "MaxAttempts": 80},
            )
        except ClientError as error:
            if codigo(error) != "DBClusterNotFoundFault":
                errores.append(f"Clúster DocumentDB: {error}")
        except WaiterError as error:
            errores.append(f"Espera del clúster DocumentDB: {error}")

    print("[3/7] Comprobando el secreto administrado...")
    secret_arn = estado.get("secret_arn")
    if secret_arn:
        try:
            secrets.describe_secret(SecretId=secret_arn)
            try:
                secrets.delete_secret(
                    SecretId=secret_arn, ForceDeleteWithoutRecovery=True
                )
            except ClientError as error:
                if codigo(error) not in {"ResourceNotFoundException", "InvalidRequestException"}:
                    errores.append(f"Secreto: {error}")
        except ClientError as error:
            if codigo(error) != "ResourceNotFoundException":
                errores.append(f"Consulta del secreto: {error}")

    print("[4/7] Eliminando el grupo de subredes...")
    subnet_group = estado.get("db_subnet_group") or estado.get("db_subnet_group_name")
    if subnet_group:
        try:
            docdb.delete_db_subnet_group(DBSubnetGroupName=subnet_group)
        except ClientError as error:
            if codigo(error) != "DBSubnetGroupNotFoundFault":
                errores.append(f"Grupo de subredes: {error}")

    print("[5/7] Terminando la EC2 del túnel...")
    ec2_id = estado.get("ec2_instance_id")
    if ec2_id:
        try:
            ec2.terminate_instances(InstanceIds=[ec2_id])
            ec2.get_waiter("instance_terminated").wait(
                InstanceIds=[ec2_id], WaiterConfig={"Delay": 15, "MaxAttempts": 40}
            )
        except ClientError as error:
            if codigo(error) != "InvalidInstanceID.NotFound":
                errores.append(f"EC2: {error}")
        except WaiterError as error:
            errores.append(f"Espera de EC2: {error}")

    print("[6/7] Eliminando la llave importada...")
    key_name = estado.get("key_name")
    if key_name:
        try:
            ec2.delete_key_pair(KeyName=key_name)
        except ClientError as error:
            if codigo(error) != "InvalidKeyPair.NotFound":
                errores.append(f"Llave EC2: {error}")

    print("[7/7] Eliminando los grupos de seguridad...")
    try:
        borrar_grupo_seguridad(ec2, estado.get("sg_documentdb_id"))
    except ClientError as error:
        errores.append(f"SG DocumentDB: {error}")
    try:
        borrar_grupo_seguridad(ec2, estado.get("sg_bastion_id") or estado.get("sg_ec2_id"))
    except ClientError as error:
        errores.append(f"SG bastión: {error}")

    if errores:
        print("\nLa eliminación quedó incompleta:")
        for error in errores:
            print(f"- {error}")
        print(f"Se conserva {STATE_FILE} para volver a intentarlo.")
        raise SystemExit(1)

    STATE_FILE.unlink(missing_ok=True)
    print("\nELIMINACIÓN COMPLETADA")
    print("No quedaron recursos registrados por este programa.")


if __name__ == "__main__":
    main()
