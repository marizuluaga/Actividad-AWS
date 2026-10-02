# Punto 2 - Infraestructura con AWS

Región utilizada: us-east-2.

La infraestructura contiene:

- Una VPC privada.
- Tres subredes.
- Grupos de seguridad para EC2 y RDS.
- Un bucket S3 privado y cifrado.
- Una instancia EC2 t3.micro sin IP pública.
- Una base de datos RDS MySQL db.t3.micro.

La infraestructura se implementará tres veces:

1. Terraform.
2. Python con Boto3.
3. AWS CLI.

Cada implementación se elimina antes de comenzar la siguiente.