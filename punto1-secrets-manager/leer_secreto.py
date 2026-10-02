import json
import os
import sys

import boto3
from botocore.exceptions import ClientError


REGION = "us-east-2"


def obtener_variable(nombre):
    valor = os.getenv(nombre)

    if not valor:
        print(f"Falta configurar la variable {nombre}.")
        sys.exit(1)

    return valor


def crear_cliente_con_rol(rol_arn):
    sts = boto3.client("sts", region_name=REGION)

    respuesta = sts.assume_role(
        RoleArn=rol_arn,
        RoleSessionName="actividad-aws-punto1"
    )

    credenciales = respuesta["Credentials"]

    return boto3.client(
        "secretsmanager",
        region_name=REGION,
        aws_access_key_id=credenciales["AccessKeyId"],
        aws_secret_access_key=credenciales["SecretAccessKey"],
        aws_session_token=credenciales["SessionToken"]
    )


def leer_secreto():
    secreto_id = obtener_variable("SECRET_ID")
    rol_arn = obtener_variable("ROLE_ARN")

    cliente = crear_cliente_con_rol(rol_arn)

    try:
        respuesta = cliente.get_secret_value(SecretId=secreto_id)
        contenido = json.loads(respuesta["SecretString"])

        usuario = contenido.get("username", "No encontrado")
        password = contenido.get("password", "")

        print("Lectura exitosa")
        print(f"Nombre o ARN: {respuesta['ARN']}")
        print(f"VersionId: {respuesta['VersionId']}")
        print(f"VersionStages: {respuesta.get('VersionStages', [])}")
        print(f"Usuario: {usuario}")
        print(f"Contraseña: {'*' * min(len(password), 12)}")
        print(f"Longitud de la contraseña: {len(password)} caracteres")

    except ClientError as error:
        codigo = error.response["Error"]["Code"]
        mensaje = error.response["Error"]["Message"]

        print(f"Error AWS: {codigo}")
        print(mensaje)
        sys.exit(1)


if __name__ == "__main__":
    leer_secreto()