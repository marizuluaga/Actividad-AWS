from __future__ import annotations

import json
from pathlib import Path

import boto3
from pymongo import MongoClient


REGION = "us-east-2"
BASE_DIR = Path(__file__).resolve().parents[1]
STATE_FILE = BASE_DIR / "infraestructura" / "estado_documentdb.json"
CA_FILE = BASE_DIR / "global-bundle.pem"


def cargar_estado() -> dict:
    if not STATE_FILE.exists():
        raise FileNotFoundError(
            f"No existe {STATE_FILE}. Primero crea la infraestructura del punto 3."
        )
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def leer_secreto(secret_arn: str) -> tuple[dict, str]:
    cliente = boto3.client("secretsmanager", region_name=REGION)
    respuesta = cliente.get_secret_value(SecretId=secret_arn)
    secreto = json.loads(respuesta["SecretString"])
    return secreto, respuesta.get("VersionId", "sin VersionId")


def mostrar(documento: dict) -> None:
    print(json.dumps(documento, ensure_ascii=False, default=str))


def main() -> None:
    estado = cargar_estado()
    secreto, version_id = leer_secreto(estado["secret_arn"])

    if not CA_FILE.exists():
        raise FileNotFoundError(
            f"Falta {CA_FILE}. Descárgalo con:\n"
            "curl -L https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem "
            "-o global-bundle.pem"
        )

    print("[1/6] Secreto recuperado correctamente desde Secrets Manager.")
    print(f"VersionId: {version_id}")
    print("La contraseña no se imprime ni está escrita en el código.")

    cliente = MongoClient(
        host="localhost",
        port=27017,
        username=secreto["username"],
        password=secreto["password"],
        tls=True,
        tlsCAFile=str(CA_FILE),
        tlsAllowInvalidHostnames=True,
        directConnection=True,
        retryWrites=False,
        serverSelectionTimeoutMS=20000,
    )

    try:
        cliente.admin.command("ping")
        print("[2/6] Conexión exitosa con Amazon DocumentDB mediante el túnel SSH.")

        coleccion = cliente["actividad_aws"]["ventas"]
        coleccion.delete_many({"actividad": "punto3"})

        documentos = [
            {"actividad": "punto3", "cliente": "Ana Gómez", "ciudad": "Medellín", "categoria": "Tecnología", "producto": "Teclado", "cantidad": 2, "precio": 180000, "estado": "Completada"},
            {"actividad": "punto3", "cliente": "Luis Pérez", "ciudad": "Bogotá", "categoria": "Tecnología", "producto": "Monitor", "cantidad": 1, "precio": 850000, "estado": "Pendiente"},
            {"actividad": "punto3", "cliente": "Carlos Ruiz", "ciudad": "Medellín", "categoria": "Tecnología", "producto": "Mouse", "cantidad": 3, "precio": 75000, "estado": "Completada"},
            {"actividad": "punto3", "cliente": "Laura Torres", "ciudad": "Cali", "categoria": "Oficina", "producto": "Escritorio", "cantidad": 1, "precio": 680000, "estado": "Pendiente"},
            {"actividad": "punto3", "cliente": "María López", "ciudad": "Bogotá", "categoria": "Oficina", "producto": "Silla", "cantidad": 2, "precio": 420000, "estado": "Completada"},
            {"actividad": "punto3", "cliente": "Andrés Castro", "ciudad": "Cali", "categoria": "Tecnología", "producto": "Portátil", "cantidad": 1, "precio": 3200000, "estado": "Completada"},
            {"actividad": "punto3", "cliente": "Sofía Ramírez", "ciudad": "Medellín", "categoria": "Oficina", "producto": "Lámpara", "cantidad": 4, "precio": 90000, "estado": "Completada"},
            {"actividad": "punto3", "cliente": "Daniela Ortiz", "ciudad": "Bogotá", "categoria": "Accesorios", "producto": "Audífonos", "cantidad": 2, "precio": 260000, "estado": "Pendiente"},
            {"actividad": "punto3", "cliente": "Jorge Vélez", "ciudad": "Medellín", "categoria": "Tecnología", "producto": "Disco SSD", "cantidad": 1, "precio": 350000, "estado": "Completada"},
            {"actividad": "punto3", "cliente": "Valentina Díaz", "ciudad": "Cali", "categoria": "Accesorios", "producto": "Cámara web", "cantidad": 2, "precio": 130000, "estado": "Completada"},
        ]

        resultado = coleccion.insert_many(documentos)
        print(f"[3/6] Documentos insertados: {len(resultado.inserted_ids)}")

        print("\n[4/6] FILTRO: ventas completadas en Medellín")
        filtro = {
            "actividad": "punto3",
            "ciudad": "Medellín",
            "estado": "Completada",
        }
        for documento in coleccion.find(filtro):
            mostrar(documento)

        print("\n[5/6] ORDENAMIENTO: productos por precio descendente")
        proyeccion = {"cliente": 1, "producto": 1, "precio": 1}
        for documento in coleccion.find(
            {"actividad": "punto3"}, proyeccion
        ).sort("precio", -1).limit(5):
            mostrar(documento)

        print("\n[6/6] AGREGACIÓN: totales por categoría")
        pipeline = [
            {"$match": {"actividad": "punto3"}},
            {
                "$group": {
                    "_id": "$categoria",
                    "unidades": {"$sum": "$cantidad"},
                    "valor_total": {
                        "$sum": {"$multiply": ["$cantidad", "$precio"]}
                    },
                }
            },
            {"$sort": {"valor_total": -1}},
        ]
        for documento in coleccion.aggregate(pipeline):
            mostrar(documento)

        total = coleccion.count_documents({"actividad": "punto3"})
        print("\nPRUEBA COMPLETADA CORRECTAMENTE")
        print(f"Total de documentos almacenados: {total}")
    finally:
        cliente.close()


if __name__ == "__main__":
    main()
