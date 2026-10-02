provider "aws" {
  region = "us-east-2"
}

# Identifica al usuario autenticado actualmente: user_cli
data "aws_caller_identity" "actual" {}

# Secreto principal de la actividad
resource "aws_secretsmanager_secret" "credenciales_prueba" {
  name                    = "actividad-aws/punto1/credenciales-prueba"
  description             = "Secreto ficticio para la actividad de AWS"
  recovery_window_in_days = 7

  tags = {
    Proyecto = "Actividad AWS"
    Punto    = "1"
    Autoras  = "Mariana Zuluaga y Valeria Perez"
  }
}

# Segundo secreto para demostrar que el programa no puede leer otros secretos
resource "aws_secretsmanager_secret" "secreto_no_autorizado" {
  name                    = "actividad-aws/punto1/secreto-no-autorizado"
  description             = "Secreto para comprobar la restriccion de permisos"
  recovery_window_in_days = 7

  tags = {
    Proyecto = "Actividad AWS"
    Punto    = "1"
    Uso      = "Prueba de acceso denegado"
  }
}

# Autoriza a user_cli para asumir el rol temporal
data "aws_iam_policy_document" "confianza_lector" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [data.aws_caller_identity.actual.arn]
    }
  }
}

# Rol que utilizará el programa Python
resource "aws_iam_role" "lector_secreto" {
  name               = "actividad-aws-punto1-lector"
  description        = "Rol con acceso limitado al secreto del punto 1"
  assume_role_policy = data.aws_iam_policy_document.confianza_lector.json
}

# Política que permite leer solamente el secreto principal
data "aws_iam_policy_document" "lectura_un_secreto" {
  statement {
    effect = "Allow"

    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret"
    ]

    resources = [
      aws_secretsmanager_secret.credenciales_prueba.arn
    ]
  }
}

# Asocia la política de lectura al rol
resource "aws_iam_role_policy" "lectura_un_secreto" {
  name   = "leer-solo-secreto-punto1"
  role   = aws_iam_role.lector_secreto.id
  policy = data.aws_iam_policy_document.lectura_un_secreto.json
}