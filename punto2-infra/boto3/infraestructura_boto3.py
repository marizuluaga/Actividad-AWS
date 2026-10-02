import json
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-2"
ARCHIVO_ESTADO = Path(__file__).with_name("recursos_boto3.json")

session = boto3.Session(region_name=REGION)
ec2 = session.client("ec2")
rds = session.client("rds")
s3 = session.client("s3")
ssm = session.client("ssm")
sts = session.client("sts")


def etiquetas(nombre):
    return [
        {"Key": "Name", "Value": nombre},
        {"Key": "Proyecto", "Value": "Actividad AWS"},
        {"Key": "Punto", "Value": "2"},
        {"Key": "Metodo", "Value": "Boto3"},
        {"Key": "Autoras", "Value": "Mariana Zuluaga y Valeria Perez"},
    ]


def guardar_estado(estado):
    ARCHIVO_ESTADO.write_text(
        json.dumps(estado, indent=2),
        encoding="utf-8",
    )


def cargar_estado():
    if not ARCHIVO_ESTADO.exists():
        raise FileNotFoundError(
            "No existe recursos_boto3.json. Primero ejecuta el modo crear."
        )

    return json.loads(ARCHIVO_ESTADO.read_text(encoding="utf-8"))


def crear():
    if ARCHIVO_ESTADO.exists():
        raise RuntimeError(
            "Ya existe recursos_boto3.json. Elimina la infraestructura anterior primero."
        )

    cuenta = sts.get_caller_identity()["Account"]
    sufijo = str(int(time.time()))
    prefijo = f"actividad-p2-boto3-{sufijo}"

    estado = {
        "region": REGION,
        "prefijo": prefijo,
        "cuenta": cuenta,
    }

    try:
        print("[1/8] Creando VPC...")

        respuesta = ec2.create_vpc(
            CidrBlock="10.30.0.0/16",
            TagSpecifications=[
                {
                    "ResourceType": "vpc",
                    "Tags": etiquetas(f"{prefijo}-vpc"),
                }
            ],
        )

        vpc_id = respuesta["Vpc"]["VpcId"]
        estado["vpc_id"] = vpc_id
        guardar_estado(estado)

        ec2.get_waiter("vpc_available").wait(VpcIds=[vpc_id])

        ec2.modify_vpc_attribute(
            VpcId=vpc_id,
            EnableDnsSupport={"Value": True},
        )

        ec2.modify_vpc_attribute(
            VpcId=vpc_id,
            EnableDnsHostnames={"Value": True},
        )

        print("[2/8] Creando subredes y tabla de rutas...")

        zonas = ec2.describe_availability_zones(
            Filters=[{"Name": "state", "Values": ["available"]}]
        )["AvailabilityZones"]

        az_a = zonas[0]["ZoneName"]
        az_b = zonas[1]["ZoneName"]

        definiciones = [
            ("aplicacion", "10.30.1.0/24", az_a),
            ("rds-a", "10.30.11.0/24", az_a),
            ("rds-b", "10.30.12.0/24", az_b),
        ]

        estado["subnet_ids"] = []

        for nombre, cidr, zona in definiciones:
            respuesta = ec2.create_subnet(
                VpcId=vpc_id,
                CidrBlock=cidr,
                AvailabilityZone=zona,
                TagSpecifications=[
                    {
                        "ResourceType": "subnet",
                        "Tags": etiquetas(f"{prefijo}-subnet-{nombre}"),
                    }
                ],
            )

            subnet_id = respuesta["Subnet"]["SubnetId"]
            estado["subnet_ids"].append(subnet_id)
            guardar_estado(estado)

        route_table = ec2.create_route_table(
            VpcId=vpc_id,
            TagSpecifications=[
                {
                    "ResourceType": "route-table",
                    "Tags": etiquetas(f"{prefijo}-rt-privada"),
                }
            ],
        )

        route_table_id = route_table["RouteTable"]["RouteTableId"]
        estado["route_table_id"] = route_table_id
        estado["route_association_ids"] = []
        guardar_estado(estado)

        for subnet_id in estado["subnet_ids"]:
            asociacion = ec2.associate_route_table(
                RouteTableId=route_table_id,
                SubnetId=subnet_id,
            )

            estado["route_association_ids"].append(
                asociacion["AssociationId"]
            )
            guardar_estado(estado)

        print("[3/8] Creando grupos de seguridad...")

        ec2_sg = ec2.create_security_group(
            GroupName=f"{prefijo}-sg-ec2",
            Description="Grupo de seguridad EC2 creado con Boto3",
            VpcId=vpc_id,
        )

        ec2_sg_id = ec2_sg["GroupId"]
        estado["ec2_sg_id"] = ec2_sg_id
        ec2.create_tags(
            Resources=[ec2_sg_id],
            Tags=etiquetas(f"{prefijo}-sg-ec2"),
        )
        guardar_estado(estado)

        rds_sg = ec2.create_security_group(
            GroupName=f"{prefijo}-sg-rds",
            Description="RDS acepta MySQL solamente desde EC2",
            VpcId=vpc_id,
        )

        rds_sg_id = rds_sg["GroupId"]
        estado["rds_sg_id"] = rds_sg_id
        ec2.create_tags(
            Resources=[rds_sg_id],
            Tags=etiquetas(f"{prefijo}-sg-rds"),
        )
        guardar_estado(estado)

        ec2.authorize_security_group_ingress(
            GroupId=rds_sg_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 3306,
                    "ToPort": 3306,
                    "UserIdGroupPairs": [
                        {"GroupId": ec2_sg_id}
                    ],
                }
            ],
        )

        print("[4/8] Creando bucket S3...")

        bucket = f"actividad-p2-boto3-{cuenta}-{sufijo}"
        estado["bucket"] = bucket

        s3.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={
                "LocationConstraint": REGION
            },
        )
        guardar_estado(estado)

        s3.put_public_access_block(
            Bucket=bucket,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )

        s3.put_bucket_encryption(
            Bucket=bucket,
            ServerSideEncryptionConfiguration={
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "AES256"
                        }
                    }
                ]
            },
        )

        s3.put_bucket_versioning(
            Bucket=bucket,
            VersioningConfiguration={"Status": "Enabled"},
        )

        s3.put_bucket_tagging(
            Bucket=bucket,
            Tagging={"TagSet": etiquetas(bucket)},
        )

        print("[5/8] Buscando Amazon Linux 2023...")

        ami = ssm.get_parameter(
            Name=(
                "/aws/service/ami-amazon-linux-latest/"
                "al2023-ami-kernel-default-x86_64"
            )
        )["Parameter"]["Value"]

        print("[6/8] Creando instancia EC2...")

        respuesta = ec2.run_instances(
            ImageId=ami,
            InstanceType="t3.micro",
            MinCount=1,
            MaxCount=1,
            SubnetId=estado["subnet_ids"][0],
            SecurityGroupIds=[ec2_sg_id],
            MetadataOptions={
                "HttpTokens": "required",
                "HttpEndpoint": "enabled",
            },
            BlockDeviceMappings=[
                {
                    "DeviceName": "/dev/xvda",
                    "Ebs": {
                        "VolumeSize": 8,
                        "VolumeType": "gp3",
                        "Encrypted": True,
                        "DeleteOnTermination": True,
                    },
                }
            ],
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": etiquetas(f"{prefijo}-ec2"),
                },
                {
                    "ResourceType": "volume",
                    "Tags": etiquetas(f"{prefijo}-volumen"),
                },
            ],
        )

        instancia_id = respuesta["Instances"][0]["InstanceId"]
        estado["instance_id"] = instancia_id
        guardar_estado(estado)

        ec2.get_waiter("instance_running").wait(
            InstanceIds=[instancia_id]
        )

        print("[7/8] Creando grupo de subredes de RDS...")

        db_subnet_group = f"{prefijo}-subnet-group"
        estado["db_subnet_group"] = db_subnet_group
        guardar_estado(estado)

        rds.create_db_subnet_group(
            DBSubnetGroupName=db_subnet_group,
            DBSubnetGroupDescription="Subredes privadas para RDS",
            SubnetIds=[
                estado["subnet_ids"][1],
                estado["subnet_ids"][2],
            ],
            Tags=etiquetas(db_subnet_group),
        )

        print("[8/8] Creando RDS con contraseña administrada...")

        rds_id = f"{prefijo}-rds"
        estado["rds_id"] = rds_id
        guardar_estado(estado)

        rds.create_db_instance(
            DBInstanceIdentifier=rds_id,
            Engine="mysql",
            DBInstanceClass="db.t4g.micro",
            AllocatedStorage=20,
            StorageType="gp3",
            StorageEncrypted=True,
            DBName="actividadp2",
            MasterUsername="adminp2",
            ManageMasterUserPassword=True,
            DBSubnetGroupName=db_subnet_group,
            VpcSecurityGroupIds=[rds_sg_id],
            PubliclyAccessible=False,
            MultiAZ=False,
            BackupRetentionPeriod=0,
            DeletionProtection=False,
            AutoMinorVersionUpgrade=True,
            Tags=etiquetas(rds_id),
        )

        print("RDS está en creación. Esto puede tardar varios minutos...")

        rds.get_waiter("db_instance_available").wait(
            DBInstanceIdentifier=rds_id,
            WaiterConfig={"Delay": 30, "MaxAttempts": 60},
        )

        base_datos = rds.describe_db_instances(
            DBInstanceIdentifier=rds_id
        )["DBInstances"][0]

        secreto = base_datos.get("MasterUserSecret", {})
        estado["secret_arn"] = secreto.get("SecretArn")
        guardar_estado(estado)

        print("\nCREACIÓN COMPLETADA")
        print(f"VPC: {vpc_id}")
        print(f"S3: {bucket}")
        print(f"EC2: {instancia_id}")
        print(f"RDS: {rds_id}")
        print(f"Secreto RDS: {estado['secret_arn']}")
        print(f"Estado guardado en: {ARCHIVO_ESTADO}")

    except Exception:
        guardar_estado(estado)
        print("\nLa creación no terminó completamente.")
        print("Los IDs creados quedaron guardados en recursos_boto3.json.")
        raise


def eliminar():
    estado = cargar_estado()

    print("[1/6] Eliminando RDS...")

    if estado.get("rds_id"):
        try:
            rds.delete_db_instance(
                DBInstanceIdentifier=estado["rds_id"],
                SkipFinalSnapshot=True,
                DeleteAutomatedBackups=True,
            )

            rds.get_waiter("db_instance_deleted").wait(
                DBInstanceIdentifier=estado["rds_id"],
                WaiterConfig={"Delay": 30, "MaxAttempts": 60},
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "DBInstanceNotFound":
                raise

    print("[2/6] Terminando EC2...")

    if estado.get("instance_id"):
        try:
            ec2.terminate_instances(
                InstanceIds=[estado["instance_id"]]
            )
            ec2.get_waiter("instance_terminated").wait(
                InstanceIds=[estado["instance_id"]]
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "InvalidInstanceID.NotFound":
                raise

    print("[3/6] Eliminando bucket S3...")

    if estado.get("bucket"):
        bucket = estado["bucket"]

        try:
            versiones = s3.get_paginator(
                "list_object_versions"
            )

            for pagina in versiones.paginate(Bucket=bucket):
                objetos = []

                for version in pagina.get("Versions", []):
                    objetos.append(
                        {
                            "Key": version["Key"],
                            "VersionId": version["VersionId"],
                        }
                    )

                for marcador in pagina.get("DeleteMarkers", []):
                    objetos.append(
                        {
                            "Key": marcador["Key"],
                            "VersionId": marcador["VersionId"],
                        }
                    )

                if objetos:
                    s3.delete_objects(
                        Bucket=bucket,
                        Delete={"Objects": objetos},
                    )

            s3.delete_bucket(Bucket=bucket)

        except ClientError as error:
            if error.response["Error"]["Code"] not in [
                "NoSuchBucket",
                "404",
            ]:
                raise

    print("[4/6] Eliminando grupo de subredes y seguridad...")

    if estado.get("db_subnet_group"):
        try:
            rds.delete_db_subnet_group(
                DBSubnetGroupName=estado["db_subnet_group"]
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "DBSubnetGroupNotFoundFault":
                raise

    for grupo in [
        estado.get("rds_sg_id"),
        estado.get("ec2_sg_id"),
    ]:
        if grupo:
            for intento in range(12):
                try:
                    ec2.delete_security_group(GroupId=grupo)
                    break
                except ClientError as error:
                    codigo = error.response["Error"]["Code"]

                    if codigo == "InvalidGroup.NotFound":
                        break

                    if codigo == "DependencyViolation":
                        time.sleep(10)
                        continue

                    raise

    print("[5/6] Eliminando subredes y tabla de rutas...")

    for asociacion in estado.get("route_association_ids", []):
        try:
            ec2.disassociate_route_table(
                AssociationId=asociacion
            )
        except ClientError:
            pass

    if estado.get("route_table_id"):
        ec2.delete_route_table(
            RouteTableId=estado["route_table_id"]
        )

    for subnet_id in estado.get("subnet_ids", []):
        ec2.delete_subnet(SubnetId=subnet_id)

    print("[6/6] Eliminando VPC...")

    if estado.get("vpc_id"):
        ec2.delete_vpc(VpcId=estado["vpc_id"])

    ARCHIVO_ESTADO.unlink(missing_ok=True)

    print("\nELIMINACIÓN COMPLETADA")
    print("No quedan recursos administrados por el programa Boto3.")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ["crear", "eliminar"]:
        print(
            "Uso: python infraestructura_boto3.py "
            "[crear|eliminar]"
        )
        sys.exit(1)

    if sys.argv[1] == "crear":
        crear()
    else:
        eliminar()


if __name__ == "__main__":
    main()