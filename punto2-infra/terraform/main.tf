data "aws_caller_identity" "actual" {}

data "aws_availability_zones" "disponibles" {
  state = "available"
}

data "aws_ssm_parameter" "amazon_linux" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "random_id" "sufijo" {
  byte_length = 3
}

locals {
  prefijo = "actividad-p2-tf"
}

# --------------------------------------------------
# RED
# --------------------------------------------------

resource "aws_vpc" "principal" {
  cidr_block           = "10.20.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${local.prefijo}-vpc"
  }
}

resource "aws_subnet" "aplicacion" {
  vpc_id                  = aws_vpc.principal.id
  cidr_block              = "10.20.1.0/24"
  availability_zone       = data.aws_availability_zones.disponibles.names[0]
  map_public_ip_on_launch = false

  tags = {
    Name = "${local.prefijo}-subnet-aplicacion"
  }
}

resource "aws_subnet" "base_datos_a" {
  vpc_id            = aws_vpc.principal.id
  cidr_block        = "10.20.11.0/24"
  availability_zone = data.aws_availability_zones.disponibles.names[0]

  tags = {
    Name = "${local.prefijo}-subnet-rds-a"
  }
}

resource "aws_subnet" "base_datos_b" {
  vpc_id            = aws_vpc.principal.id
  cidr_block        = "10.20.12.0/24"
  availability_zone = data.aws_availability_zones.disponibles.names[1]

  tags = {
    Name = "${local.prefijo}-subnet-rds-b"
  }
}

resource "aws_route_table" "privada" {
  vpc_id = aws_vpc.principal.id

  tags = {
    Name = "${local.prefijo}-rt-privada"
  }
}

resource "aws_route_table_association" "aplicacion" {
  subnet_id      = aws_subnet.aplicacion.id
  route_table_id = aws_route_table.privada.id
}

resource "aws_route_table_association" "base_datos_a" {
  subnet_id      = aws_subnet.base_datos_a.id
  route_table_id = aws_route_table.privada.id
}

resource "aws_route_table_association" "base_datos_b" {
  subnet_id      = aws_subnet.base_datos_b.id
  route_table_id = aws_route_table.privada.id
}

# --------------------------------------------------
# GRUPOS DE SEGURIDAD
# --------------------------------------------------

resource "aws_security_group" "ec2" {
  name        = "${local.prefijo}-sg-ec2"
  description = "Grupo de seguridad de la instancia EC2"
  vpc_id      = aws_vpc.principal.id

  tags = {
    Name = "${local.prefijo}-sg-ec2"
  }
}

resource "aws_vpc_security_group_egress_rule" "ec2_salida" {
  security_group_id = aws_security_group.ec2.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_security_group" "rds" {
  name        = "${local.prefijo}-sg-rds"
  description = "RDS acepta MySQL solamente desde EC2"
  vpc_id      = aws_vpc.principal.id

  tags = {
    Name = "${local.prefijo}-sg-rds"
  }
}

resource "aws_vpc_security_group_ingress_rule" "mysql_desde_ec2" {
  security_group_id            = aws_security_group.rds.id
  referenced_security_group_id = aws_security_group.ec2.id
  from_port                    = 3306
  to_port                      = 3306
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "rds_salida" {
  security_group_id = aws_security_group.rds.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# --------------------------------------------------
# S3
# --------------------------------------------------

resource "aws_s3_bucket" "archivos" {
  bucket        = "${local.prefijo}-${data.aws_caller_identity.actual.account_id}-${random_id.sufijo.hex}"
  force_destroy = true

  tags = {
    Name = "${local.prefijo}-s3"
  }
}

resource "aws_s3_bucket_public_access_block" "archivos" {
  bucket = aws_s3_bucket.archivos.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "archivos" {
  bucket = aws_s3_bucket.archivos.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "archivos" {
  bucket = aws_s3_bucket.archivos.id

  versioning_configuration {
    status = "Enabled"
  }
}

# --------------------------------------------------
# EC2
# --------------------------------------------------

resource "aws_instance" "servidor" {
  ami                         = data.aws_ssm_parameter.amazon_linux.value
  instance_type               = "t3.micro"
  subnet_id                   = aws_subnet.aplicacion.id
  vpc_security_group_ids      = [aws_security_group.ec2.id]
  associate_public_ip_address = false

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 8
    encrypted             = true
    delete_on_termination = true
  }

  tags = {
    Name = "${local.prefijo}-ec2"
  }
}

# --------------------------------------------------
# RDS MYSQL
# --------------------------------------------------

resource "aws_db_subnet_group" "principal" {
  name = "${local.prefijo}-subnet-group"

  subnet_ids = [
    aws_subnet.base_datos_a.id,
    aws_subnet.base_datos_b.id
  ]

  tags = {
    Name = "${local.prefijo}-subnet-group"
  }
}

resource "aws_db_instance" "mysql" {
  identifier = "${local.prefijo}-rds-${random_id.sufijo.hex}"

  engine         = "mysql"
  instance_class = "db.t3.micro"

  allocated_storage = 20
  storage_type      = "gp3"
  storage_encrypted = true

  db_name                     = "actividadp2"
  username                    = "adminp2"
  manage_master_user_password = true


  db_subnet_group_name   = aws_db_subnet_group.principal.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  publicly_accessible     = false
  multi_az                = false
  backup_retention_period = 0

  skip_final_snapshot      = true
  delete_automated_backups = true
  deletion_protection      = false
  apply_immediately        = true

  tags = {
    Name = "${local.prefijo}-rds"
  }
}