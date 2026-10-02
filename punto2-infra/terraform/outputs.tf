output "identidad_aws" {
  value = data.aws_caller_identity.actual.arn
}

output "vpc_id" {
  value = aws_vpc.principal.id
}

output "bucket_s3" {
  value = aws_s3_bucket.archivos.id
}

output "instancia_ec2" {
  value = aws_instance.servidor.id
}

output "ip_privada_ec2" {
  value = aws_instance.servidor.private_ip
}

output "rds_identificador" {
  value = aws_db_instance.mysql.identifier
}

output "rds_endpoint" {
  value = aws_db_instance.mysql.endpoint
}
output "rds_secret_arn" {
  description = "ARN del secreto administrado por RDS"
  value       = try(aws_db_instance.mysql.master_user_secret[0].secret_arn, null)
}