#!/usr/bin/env bash
set -euo pipefail

export AWS_PAGER=""

REGION="us-east-2"
SUFIJO="$(date +%s)"
CUENTA="$(aws sts get-caller-identity --query Account --output text)"
PREFIJO="actividad-p2-cli-${SUFIJO}"
ARCHIVO_ESTADO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/recursos_cli.env"

VPC_ID=""
SUBNET_APP=""
SUBNET_RDS_A=""
SUBNET_RDS_B=""
ROUTE_TABLE_ID=""
ASSOC_APP=""
ASSOC_RDS_A=""
ASSOC_RDS_B=""
EC2_SG_ID=""
RDS_SG_ID=""
BUCKET=""
INSTANCE_ID=""
DB_SUBNET_GROUP=""
RDS_ID=""
SECRET_ARN=""

guardar_estado() {
    {
        printf 'REGION=%q\n' "$REGION"
        printf 'PREFIJO=%q\n' "$PREFIJO"
        printf 'VPC_ID=%q\n' "$VPC_ID"
        printf 'SUBNET_APP=%q\n' "$SUBNET_APP"
        printf 'SUBNET_RDS_A=%q\n' "$SUBNET_RDS_A"
        printf 'SUBNET_RDS_B=%q\n' "$SUBNET_RDS_B"
        printf 'ROUTE_TABLE_ID=%q\n' "$ROUTE_TABLE_ID"
        printf 'ASSOC_APP=%q\n' "$ASSOC_APP"
        printf 'ASSOC_RDS_A=%q\n' "$ASSOC_RDS_A"
        printf 'ASSOC_RDS_B=%q\n' "$ASSOC_RDS_B"
        printf 'EC2_SG_ID=%q\n' "$EC2_SG_ID"
        printf 'RDS_SG_ID=%q\n' "$RDS_SG_ID"
        printf 'BUCKET=%q\n' "$BUCKET"
        printf 'INSTANCE_ID=%q\n' "$INSTANCE_ID"
        printf 'DB_SUBNET_GROUP=%q\n' "$DB_SUBNET_GROUP"
        printf 'RDS_ID=%q\n' "$RDS_ID"
        printf 'SECRET_ARN=%q\n' "$SECRET_ARN"
    } > "$ARCHIVO_ESTADO"
}

etiquetar_ec2() {
    aws ec2 create-tags \
        --resources "$1" \
        --tags \
        "Key=Name,Value=$2" \
        "Key=Proyecto,Value=ActividadAWS" \
        "Key=Punto,Value=2" \
        "Key=Metodo,Value=CLI" \
        --region "$REGION"
}

if [[ -f "$ARCHIVO_ESTADO" ]]; then
    echo "Ya existe recursos_cli.env."
    echo "Primero deben eliminarse los recursos anteriores."
    exit 1
fi

echo "[1/8] Creando VPC..."

VPC_ID="$(aws ec2 create-vpc \
    --cidr-block 10.40.0.0/16 \
    --region "$REGION" \
    --query 'Vpc.VpcId' \
    --output text)"

guardar_estado
etiquetar_ec2 "$VPC_ID" "${PREFIJO}-vpc"

aws ec2 wait vpc-available \
    --vpc-ids "$VPC_ID" \
    --region "$REGION"

aws ec2 modify-vpc-attribute \
    --vpc-id "$VPC_ID" \
    --enable-dns-support '{"Value":true}' \
    --region "$REGION"

aws ec2 modify-vpc-attribute \
    --vpc-id "$VPC_ID" \
    --enable-dns-hostnames '{"Value":true}' \
    --region "$REGION"

echo "[2/8] Consultando zonas de disponibilidad..."

AZ_A="$(aws ec2 describe-availability-zones \
    --region "$REGION" \
    --filters Name=state,Values=available \
    --query 'AvailabilityZones[0].ZoneName' \
    --output text)"

AZ_B="$(aws ec2 describe-availability-zones \
    --region "$REGION" \
    --filters Name=state,Values=available \
    --query 'AvailabilityZones[1].ZoneName' \
    --output text)"

echo "[3/8] Creando subredes..."

SUBNET_APP="$(aws ec2 create-subnet \
    --vpc-id "$VPC_ID" \
    --cidr-block 10.40.1.0/24 \
    --availability-zone "$AZ_A" \
    --region "$REGION" \
    --query 'Subnet.SubnetId' \
    --output text)"

guardar_estado
etiquetar_ec2 "$SUBNET_APP" "${PREFIJO}-subnet-aplicacion"

SUBNET_RDS_A="$(aws ec2 create-subnet \
    --vpc-id "$VPC_ID" \
    --cidr-block 10.40.11.0/24 \
    --availability-zone "$AZ_A" \
    --region "$REGION" \
    --query 'Subnet.SubnetId' \
    --output text)"

guardar_estado
etiquetar_ec2 "$SUBNET_RDS_A" "${PREFIJO}-subnet-rds-a"

SUBNET_RDS_B="$(aws ec2 create-subnet \
    --vpc-id "$VPC_ID" \
    --cidr-block 10.40.12.0/24 \
    --availability-zone "$AZ_B" \
    --region "$REGION" \
    --query 'Subnet.SubnetId' \
    --output text)"

guardar_estado
etiquetar_ec2 "$SUBNET_RDS_B" "${PREFIJO}-subnet-rds-b"

ROUTE_TABLE_ID="$(aws ec2 create-route-table \
    --vpc-id "$VPC_ID" \
    --region "$REGION" \
    --query 'RouteTable.RouteTableId' \
    --output text)"

guardar_estado
etiquetar_ec2 "$ROUTE_TABLE_ID" "${PREFIJO}-rt-privada"

ASSOC_APP="$(aws ec2 associate-route-table \
    --route-table-id "$ROUTE_TABLE_ID" \
    --subnet-id "$SUBNET_APP" \
    --region "$REGION" \
    --query 'AssociationId' \
    --output text)"

ASSOC_RDS_A="$(aws ec2 associate-route-table \
    --route-table-id "$ROUTE_TABLE_ID" \
    --subnet-id "$SUBNET_RDS_A" \
    --region "$REGION" \
    --query 'AssociationId' \
    --output text)"

ASSOC_RDS_B="$(aws ec2 associate-route-table \
    --route-table-id "$ROUTE_TABLE_ID" \
    --subnet-id "$SUBNET_RDS_B" \
    --region "$REGION" \
    --query 'AssociationId' \
    --output text)"

guardar_estado

echo "[4/8] Creando grupos de seguridad..."

EC2_SG_ID="$(aws ec2 create-security-group \
    --group-name "${PREFIJO}-sg-ec2" \
    --description "Grupo de seguridad EC2 creado con AWS CLI" \
    --vpc-id "$VPC_ID" \
    --region "$REGION" \
    --query 'GroupId' \
    --output text)"

guardar_estado
etiquetar_ec2 "$EC2_SG_ID" "${PREFIJO}-sg-ec2"

RDS_SG_ID="$(aws ec2 create-security-group \
    --group-name "${PREFIJO}-sg-rds" \
    --description "RDS acepta MySQL solamente desde EC2" \
    --vpc-id "$VPC_ID" \
    --region "$REGION" \
    --query 'GroupId' \
    --output text)"

guardar_estado
etiquetar_ec2 "$RDS_SG_ID" "${PREFIJO}-sg-rds"

aws ec2 authorize-security-group-ingress \
    --group-id "$RDS_SG_ID" \
    --protocol tcp \
    --port 3306 \
    --source-group "$EC2_SG_ID" \
    --region "$REGION"

echo "[5/8] Creando bucket S3..."

BUCKET="actividad-p2-cli-${CUENTA}-${SUFIJO}"
guardar_estado

aws s3api create-bucket \
    --bucket "$BUCKET" \
    --region "$REGION" \
    --create-bucket-configuration "LocationConstraint=$REGION"

aws s3api put-public-access-block \
    --bucket "$BUCKET" \
    --public-access-block-configuration \
    'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true'

aws s3api put-bucket-encryption \
    --bucket "$BUCKET" \
    --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

aws s3api put-bucket-versioning \
    --bucket "$BUCKET" \
    --versioning-configuration Status=Enabled

echo "[6/8] Creando instancia EC2..."

AMI_ID="$(aws ssm get-parameter \
    --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
    --region "$REGION" \
    --query 'Parameter.Value' \
    --output text)"

INSTANCE_ID="$(aws ec2 run-instances \
    --image-id "$AMI_ID" \
    --instance-type t3.micro \
    --subnet-id "$SUBNET_APP" \
    --security-group-ids "$EC2_SG_ID" \
    --metadata-options HttpTokens=required,HttpEndpoint=enabled \
    --block-device-mappings \
    '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":8,"VolumeType":"gp3","Encrypted":true,"DeleteOnTermination":true}}]' \
    --tag-specifications \
    "ResourceType=instance,Tags=[{Key=Name,Value=${PREFIJO}-ec2},{Key=Proyecto,Value=ActividadAWS},{Key=Punto,Value=2},{Key=Metodo,Value=CLI}]" \
    --region "$REGION" \
    --query 'Instances[0].InstanceId' \
    --output text)"

guardar_estado

aws ec2 wait instance-running \
    --instance-ids "$INSTANCE_ID" \
    --region "$REGION"

echo "[7/8] Creando grupo de subredes de RDS..."

DB_SUBNET_GROUP="${PREFIJO}-subnet-group"
guardar_estado

aws rds create-db-subnet-group \
    --db-subnet-group-name "$DB_SUBNET_GROUP" \
    --db-subnet-group-description "Subredes privadas para RDS" \
    --subnet-ids "$SUBNET_RDS_A" "$SUBNET_RDS_B" \
    --tags \
    "Key=Name,Value=$DB_SUBNET_GROUP" \
    "Key=Proyecto,Value=ActividadAWS" \
    "Key=Punto,Value=2" \
    "Key=Metodo,Value=CLI" \
    --region "$REGION" \
    >/dev/null

echo "[8/8] Creando RDS con contraseña en Secrets Manager..."

RDS_ID="${PREFIJO}-rds"
guardar_estado

aws rds create-db-instance \
    --db-instance-identifier "$RDS_ID" \
    --engine mysql \
    --db-instance-class db.t4g.micro \
    --allocated-storage 20 \
    --storage-type gp3 \
    --storage-encrypted \
    --db-name actividadp2 \
    --master-username adminp2 \
    --manage-master-user-password \
    --db-subnet-group-name "$DB_SUBNET_GROUP" \
    --vpc-security-group-ids "$RDS_SG_ID" \
    --no-publicly-accessible \
    --no-multi-az \
    --backup-retention-period 0 \
    --no-deletion-protection \
    --tags \
    "Key=Name,Value=$RDS_ID" \
    "Key=Proyecto,Value=ActividadAWS" \
    "Key=Punto,Value=2" \
    "Key=Metodo,Value=CLI" \
    --region "$REGION" \
    >/dev/null

echo "RDS está en creación. Esto puede tardar varios minutos..."

aws rds wait db-instance-available \
    --db-instance-identifier "$RDS_ID" \
    --region "$REGION"

SECRET_ARN="$(aws rds describe-db-instances \
    --db-instance-identifier "$RDS_ID" \
    --region "$REGION" \
    --query 'DBInstances[0].MasterUserSecret.SecretArn' \
    --output text)"

guardar_estado

echo
echo "CREACIÓN CON AWS CLI COMPLETADA"
echo "VPC: $VPC_ID"
echo "S3: $BUCKET"
echo "EC2: $INSTANCE_ID"
echo "RDS: $RDS_ID"
echo "Secreto RDS: $SECRET_ARN"