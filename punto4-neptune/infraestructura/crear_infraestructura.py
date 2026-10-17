from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, ParamValidationError


REGION = "us-east-2"
STATE_FILE = Path(__file__).with_name("estado_neptune.json")
AMI_PARAMETER = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"


def guardar_estado(estado: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def etiquetas(nombre: str) -> list[dict]:
    return [
        {"Key": "Name", "Value": nombre},
        {"Key": "Proyecto", "Value": "Actividad AWS"},
        {"Key": "Punto", "Value": "4"},
        {"Key": "Autoras", "Value": "Mariana Zuluaga y Valeria Perez"},
    ]


def main() -> None:
    ip_permitida = os.environ.get("MY_IP", "").strip()
    llave_publica = os.environ.get("SSH_PUBLIC_KEY_FILE", "").strip()
    if not ip_permitida.endswith("/32"):
        sys.exit("Define MY_IP con formato x.x.x.x/32.")
    if not llave_publica or not Path(llave_publica).expanduser().is_file():
        sys.exit("Define SSH_PUBLIC_KEY_FILE con la ruta de la llave pública .pub.")

    sufijo = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    prefijo = f"actividad-p4-{sufijo}"
    estado = {"region": REGION, "prefix": prefijo}
    guardar_estado(estado)

    ec2 = boto3.client("ec2", region_name=REGION)
    ssm = boto3.client("ssm", region_name=REGION)
    neptune = boto3.client("neptune", region_name=REGION)

    try:
        print("[1/8] Consultando la VPC y las subredes predeterminadas...")
        vpcs = ec2.describe_vpcs(
            Filters=[{"Name": "is-default", "Values": ["true"]}]
        )["Vpcs"]
        if not vpcs:
            raise RuntimeError("La región no tiene una VPC predeterminada.")
        vpc_id = vpcs[0]["VpcId"]

        subredes = ec2.describe_subnets(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "default-for-az", "Values": ["true"]},
            ]
        )["Subnets"]
        subredes = sorted(subredes, key=lambda item: item["AvailabilityZone"])
        if len({item["AvailabilityZone"] for item in subredes}) < 2:
            raise RuntimeError("Neptune necesita subredes en por lo menos dos zonas.")
        subnet_ids = [item["SubnetId"] for item in subredes]
        estado.update({"vpc_id": vpc_id, "subnet_ids": subnet_ids})
        guardar_estado(estado)
        print(f"VPC encontrada: {vpc_id}")
        print(f"Subredes encontradas: {subnet_ids}")

        print("[2/8] Creando los grupos de seguridad...")
        sg_bastion_nombre = f"{prefijo}-sg-bastion"
        sg_neptune_nombre = f"{prefijo}-sg-neptune"
        sg_bastion = ec2.create_security_group(
            GroupName=sg_bastion_nombre,
            Description="SSH desde la IP de las autoras",
            VpcId=vpc_id,
            TagSpecifications=[
                {"ResourceType": "security-group", "Tags": etiquetas(sg_bastion_nombre)}
            ],
        )["GroupId"]
        estado["sg_bastion_id"] = sg_bastion
        guardar_estado(estado)
        ec2.authorize_security_group_ingress(
            GroupId=sg_bastion,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 22,
                    "ToPort": 22,
                    "IpRanges": [
                        {"CidrIp": ip_permitida, "Description": "SSH desde mi IP"}
                    ],
                }
            ],
        )

        sg_neptune = ec2.create_security_group(
            GroupName=sg_neptune_nombre,
            Description="Neptune solo desde la EC2 bastion",
            VpcId=vpc_id,
            TagSpecifications=[
                {"ResourceType": "security-group", "Tags": etiquetas(sg_neptune_nombre)}
            ],
        )["GroupId"]
        estado["sg_neptune_id"] = sg_neptune
        guardar_estado(estado)
        ec2.authorize_security_group_ingress(
            GroupId=sg_neptune,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 8182,
                    "ToPort": 8182,
                    "UserIdGroupPairs": [
                        {"GroupId": sg_bastion, "Description": "Gremlin desde el bastion"}
                    ],
                }
            ],
        )

        print("[3/8] Importando la llave pública en EC2...")
        key_name = f"{prefijo}-key"
        ec2.import_key_pair(
            KeyName=key_name,
            PublicKeyMaterial=Path(llave_publica).expanduser().read_bytes(),
            TagSpecifications=[
                {"ResourceType": "key-pair", "Tags": etiquetas(key_name)}
            ],
        )
        estado["key_name"] = key_name
        guardar_estado(estado)

        print("[4/8] Creando el grupo de subredes de Neptune...")
        subnet_group = f"{prefijo}-subnet-group"
        neptune.create_db_subnet_group(
            DBSubnetGroupName=subnet_group,
            DBSubnetGroupDescription="Subredes para Neptune de la actividad",
            SubnetIds=subnet_ids,
            Tags=etiquetas(subnet_group),
        )
        estado["db_subnet_group"] = subnet_group
        guardar_estado(estado)

        print("[5/8] Creando la EC2 para el túnel SSH...")
        ami_id = ssm.get_parameter(Name=AMI_PARAMETER)["Parameter"]["Value"]
        respuesta = ec2.run_instances(
            ImageId=ami_id,
            InstanceType="t3.micro",
            MinCount=1,
            MaxCount=1,
            KeyName=key_name,
            NetworkInterfaces=[
                {
                    "DeviceIndex": 0,
                    "SubnetId": subnet_ids[0],
                    "Groups": [sg_bastion],
                    "AssociatePublicIpAddress": True,
                }
            ],
            BlockDeviceMappings=[
                {
                    "DeviceName": "/dev/xvda",
                    "Ebs": {
                        "VolumeSize": 8,
                        "VolumeType": "gp3",
                        "DeleteOnTermination": True,
                    },
                }
            ],
            TagSpecifications=[
                {"ResourceType": "instance", "Tags": etiquetas(f"{prefijo}-bastion")},
                {"ResourceType": "volume", "Tags": etiquetas(f"{prefijo}-volumen")},
            ],
        )
        instance_id = respuesta["Instances"][0]["InstanceId"]
        estado["ec2_instance_id"] = instance_id
        guardar_estado(estado)
        print("Esperando a que la EC2 quede encendida...")
        ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
        instancia = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]
        public_ip = instancia["PublicIpAddress"]
        estado["ec2_public_ip"] = public_ip
        guardar_estado(estado)
        print(f"EC2 creada: {instance_id}")
        print(f"IP pública: {public_ip}")

        versiones = neptune.describe_db_engine_versions(
            Engine="neptune", DefaultOnly=True
        )["DBEngineVersions"]
        engine_version = versiones[0]["EngineVersion"] if versiones else "1.4.8.0"

        print("[6/8] Creando el clúster Neptune Serverless...")
        cluster_id = f"{prefijo}-cluster"
        neptune.create_db_cluster(
            DBClusterIdentifier=cluster_id,
            Engine="neptune",
            EngineVersion=engine_version,
            Port=8182,
            DBSubnetGroupName=subnet_group,
            VpcSecurityGroupIds=[sg_neptune],
            StorageEncrypted=True,
            BackupRetentionPeriod=1,
            DeletionProtection=False,
            EnableIAMDatabaseAuthentication=False,
            ServerlessV2ScalingConfiguration={"MinCapacity": 1.0, "MaxCapacity": 2.5},
            Tags=etiquetas(cluster_id),
        )
        estado["neptune_cluster_id"] = cluster_id
        guardar_estado(estado)

        print("[7/8] Creando la instancia Serverless de Neptune...")
        neptune_instance_id = f"{prefijo}-instance"
        neptune.create_db_instance(
            DBInstanceIdentifier=neptune_instance_id,
            DBInstanceClass="db.serverless",
            Engine="neptune",
            DBClusterIdentifier=cluster_id,
            Tags=etiquetas(neptune_instance_id),
        )
        estado["neptune_instance_id"] = neptune_instance_id
        guardar_estado(estado)
        print("Este paso puede tardar varios minutos.")
        neptune.get_waiter("db_instance_available").wait(
            DBInstanceIdentifier=neptune_instance_id,
            WaiterConfig={"Delay": 30, "MaxAttempts": 80},
        )

        cluster = neptune.describe_db_clusters(DBClusterIdentifier=cluster_id)["DBClusters"][0]
        endpoint = cluster["Endpoint"]
        estado.update({"neptune_endpoint": endpoint, "neptune_port": 8182})
        guardar_estado(estado)

        print("[8/8] Infraestructura creada correctamente.")
        print("\nRESUMEN")
        print(f"Región: {REGION}")
        print(f"EC2: {instance_id}")
        print(f"IP pública EC2: {public_ip}")
        print(f"Clúster: {cluster_id}")
        print(f"Instancia Neptune: {neptune_instance_id}")
        print(f"Endpoint: {endpoint}:8182")
        print(f"Estado guardado en: {STATE_FILE}")

    except (ClientError, ParamValidationError, RuntimeError, KeyError) as error:
        guardar_estado(estado)
        print("\nLa creación no terminó.")
        print("Los identificadores creados quedaron guardados.")
        print(f"Error: {error}")
        print("\nEjecuta infraestructura/eliminar_infraestructura.py para realizar la limpieza.")
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
