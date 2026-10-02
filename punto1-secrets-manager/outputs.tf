output "identidad_aws" {
  description = "Identidad que ejecuta Terraform"
  value       = data.aws_caller_identity.actual.arn
}

output "nombre_secreto" {
  description = "Nombre del secreto permitido"
  value       = aws_secretsmanager_secret.credenciales_prueba.name
}

output "arn_secreto" {
  description = "ARN del secreto permitido"
  value       = aws_secretsmanager_secret.credenciales_prueba.arn
}

output "arn_secreto_no_autorizado" {
  description = "ARN utilizado para probar AccessDenied"
  value       = aws_secretsmanager_secret.secreto_no_autorizado.arn
}

output "arn_rol_lector" {
  description = "Rol limitado que utilizara Python"
  value       = aws_iam_role.lector_secreto.arn
}