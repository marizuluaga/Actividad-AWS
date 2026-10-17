from __future__ import annotations

import json
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, WaiterError


REGION = "us-east-2"
STATE_FILE = Path(__file__).with_name("estado_neptune.json")


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

    region = estado.get("region", REGION)
    neptune = boto3.client("neptune", region_name=region)
    ec2 = boto3.client("ec2", region_name=region)
    errores: list[str] = []

    print("[1/6] Eliminando la instancia de Neptune...")
    instance_id = estado.get("neptune_instance_id") or estado.get("db_instance_identifier")
    if instance_id:
        try:
            neptune.delete_db_instance(DBInstanceIdentifier=instance_id)
            neptune.get_waiter("db_instance_deleted").wait(
                DBInstanceIdentifier=instance_id,
                WaiterConfig={"Delay": 30, "MaxAttempts": 80},
            )
        except ClientError as error:
            if codigo(error) != "DBInstanceNotFound":
                errores.append(f"Instancia Neptune: {error}")
        except WaiterError as error:
            errores.append(f"Espera de instancia Neptune: {error}")

    print("[2/6] Eliminando el clúster de Neptune sin snapshot final...")
    cluster_id = estado.get("neptune_cluster_id") or estado.get("db_cluster_identifier")
    if cluster_id:
        try:
            neptune.delete_db_cluster(
                DBClusterIdentifier=cluster_id, SkipFinalSnapshot=True
            )
            neptune.get_waiter("db_cluster_deleted").wait(
                DBClusterIdentifier=cluster_id,
                WaiterConfig={"Delay": 30, "MaxAttempts": 80},
            )
        except ClientError as error:
            if codigo(error) != "DBClusterNotFoundFault":
                errores.append(f"Clúster Neptune: {error}")
        except WaiterError as error:
            errores.append(f"Espera del clúster Neptune: {error}")

    print("[3/6] Eliminando el grupo de subredes...")
    subnet_group = estado.get("db_subnet_group") or estado.get("db_subnet_group_name")
    if subnet_group:
        try:
            neptune.delete_db_subnet_group(DBSubnetGroupName=subnet_group)
        except ClientError as error:
            if codigo(error) != "DBSubnetGroupNotFoundFault":
                errores.append(f"Grupo de subredes: {error}")

    print("[4/6] Terminando la EC2 del túnel...")
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

    print("[5/6] Eliminando la llave importada...")
    key_name = estado.get("key_name")
    if key_name:
        try:
            ec2.delete_key_pair(KeyName=key_name)
        except ClientError as error:
            if codigo(error) != "InvalidKeyPair.NotFound":
                errores.append(f"Llave EC2: {error}")

    print("[6/6] Eliminando los grupos de seguridad...")
    try:
        borrar_grupo_seguridad(ec2, estado.get("sg_neptune_id"))
    except ClientError as error:
        errores.append(f"SG Neptune: {error}")
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
