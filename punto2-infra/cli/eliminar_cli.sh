#!/usr/bin/env bash
set -euo pipefail

export AWS_PAGER=""

DIRECTORIO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCHIVO_ESTADO="${DIRECTORIO}/recursos_cli.env"

if [[ ! -f "$ARCHIVO_ESTADO" ]]; then
    echo "No existe recursos_cli.env."
    echo "No se encontraron identificadores para eliminar."
    exit 1
fi

source "$ARCHIVO_ESTADO"

reintentar() {
    local descripcion="$1"
    shift

    for intento in {1..20}; do
        if "$@" >/dev/null 2>&1; then
            echo "$descripcion eliminado."
            return 0
        fi

        echo "Esperando para eliminar $descripcion..."
        sleep 10
    done

    echo "No fue posible eliminar $descripcion."
    return 1
}

echo "[1/7] Eliminando RDS..."

if [[ -n "${RDS_ID:-}" ]] && aws rds describe-db-instances \
    --db-instance-identifier "$RDS_ID" \
    --region "$REGION" \
    >/dev/null 2>&1
then
    aws rds delete-db-instance \
        --db-instance-identifier "$RDS_ID" \
        --skip-final-snapshot \
        --delete-automated-backups \
        --region "$REGION" \
        >/dev/null

    echo "Esperando la eliminación completa de RDS..."

    aws rds wait db-instance-deleted \
        --db-instance-identifier "$RDS_ID" \
        --region "$REGION"
fi

echo "[2/7] Terminando EC2..."

if [[ -n "${INSTANCE_ID:-}" ]]; then
    aws ec2 terminate-instances \
        --instance-ids "$INSTANCE_ID" \
        --region "$REGION" \
        >/dev/null

    aws ec2 wait instance-terminated \
        --instance-ids "$INSTANCE_ID" \
        --region "$REGION"
fi

echo "[3/7] Eliminando bucket S3..."

if [[ -n "${BUCKET:-}" ]] && aws s3api head-bucket \
    --bucket "$BUCKET" \
    >/dev/null 2>&1
then
    aws s3 rm "s3://${BUCKET}" --recursive >/dev/null

    aws s3api delete-bucket \
        --bucket "$BUCKET" \
        --region "$REGION"
fi

echo "[4/7] Eliminando grupo de subredes de RDS..."

if [[ -n "${DB_SUBNET_GROUP:-}" ]]; then
    aws rds delete-db-subnet-group \
        --db-subnet-group-name "$DB_SUBNET_GROUP" \
        --region "$REGION" \
        >/dev/null 2>&1 || true
fi

echo "[5/7] Eliminando grupos de seguridad..."

if [[ -n "${RDS_SG_ID:-}" ]]; then
    reintentar \
        "grupo de seguridad RDS" \
        aws ec2 delete-security-group \
        --group-id "$RDS_SG_ID" \
        --region "$REGION"
fi

if [[ -n "${EC2_SG_ID:-}" ]]; then
    reintentar \
        "grupo de seguridad EC2" \
        aws ec2 delete-security-group \
        --group-id "$EC2_SG_ID" \
        --region "$REGION"
fi

echo "[6/7] Eliminando tabla de rutas y subredes..."

for asociacion in \
    "${ASSOC_APP:-}" \
    "${ASSOC_RDS_A:-}" \
    "${ASSOC_RDS_B:-}"
do
    if [[ -n "$asociacion" ]]; then
        aws ec2 disassociate-route-table \
            --association-id "$asociacion" \
            --region "$REGION" \
            >/dev/null 2>&1 || true
    fi
done

if [[ -n "${ROUTE_TABLE_ID:-}" ]]; then
    reintentar \
        "tabla de rutas" \
        aws ec2 delete-route-table \
        --route-table-id "$ROUTE_TABLE_ID" \
        --region "$REGION"
fi

for subnet in \
    "${SUBNET_APP:-}" \
    "${SUBNET_RDS_A:-}" \
    "${SUBNET_RDS_B:-}"
do
    if [[ -n "$subnet" ]]; then
        reintentar \
            "subred $subnet" \
            aws ec2 delete-subnet \
            --subnet-id "$subnet" \
            --region "$REGION"
    fi
done

echo "[7/7] Eliminando VPC..."

if [[ -n "${VPC_ID:-}" ]]; then
    reintentar \
        "VPC" \
        aws ec2 delete-vpc \
        --vpc-id "$VPC_ID" \
        --region "$REGION"
fi

rm -f "$ARCHIVO_ESTADO"

echo
echo "ELIMINACIÓN CON AWS CLI COMPLETADA"
echo "No quedan recursos activos de la implementación CLI."