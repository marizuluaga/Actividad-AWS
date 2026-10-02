terraform {
  required_version = ">= 1.16.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }

    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }
}

provider "aws" {
  region = "us-east-2"

  default_tags {
    tags = {
      Proyecto = "Actividad AWS"
      Punto    = "2"
      Metodo   = "Terraform"
      Autoras  = "Mariana Zuluaga y Valeria Perez"
    }
  }
}