from __future__ import annotations

import csv
import json
import time
from pathlib import Path

import boto3


BASE_DIR = Path(__file__).resolve().parents[1]
STATE_FILE = BASE_DIR / "infraestructura" / "estado_glue_athena.json"
NEW_DATA_FILE = BASE_DIR / "datos" / "ventas_nuevas.csv"


def cargar() -> dict:
    if not STATE_FILE.exists():
        raise FileNotFoundError("Primero crea la infraestructura del punto 6.")
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def ejecutar_athena(athena, estado: dict, sql: str) -> str:
    respuesta = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": estado["database_name"]},
        ResultConfiguration={
            "OutputLocation": f"s3://{estado['bucket_name']}/{estado['results_prefix']}"
        },
        WorkGroup="primary",
    )
    query_id = respuesta["QueryExecutionId"]
    while True:
        detalle = athena.get_query_execution(QueryExecutionId=query_id)
        ejecucion = detalle["QueryExecution"]
        status = ejecucion["Status"]["State"]
        if status == "SUCCEEDED":
            return query_id
        if status in {"FAILED", "CANCELLED"}:
            razon = ejecucion["Status"].get("StateChangeReason", "sin detalle")
            raise RuntimeError(f"Athena terminó en {status}: {razon}")
        time.sleep(2)


def mostrar(athena, query_id: str) -> None:
    paginator = athena.get_paginator("get_query_results")
    primera = True
    for pagina in paginator.paginate(QueryExecutionId=query_id):
        for fila in pagina["ResultSet"]["Rows"]:
            valores = [campo.get("VarCharValue", "NULL") for campo in fila["Data"]]
            if primera:
                print(" | ".join(valores))
                print("-" * 90)
                primera = False
            else:
                print(" | ".join(valores))


def esperar_crawler(glue, nombre: str) -> None:
    time.sleep(5)
    for _ in range(120):
        crawler = glue.get_crawler(Name=nombre)["Crawler"]
        status = crawler.get("LastCrawl", {}).get("Status")
        print(f"  Crawler: {crawler['State']}; última ejecución: {status}")
        if crawler["State"] == "READY" and status == "SUCCEEDED":
            return
        if crawler["State"] == "READY" and status in {"FAILED", "CANCELLED"}:
            raise RuntimeError(f"El crawler terminó con estado {status}")
        time.sleep(10)
    raise TimeoutError("El crawler no terminó.")


def crear_archivo_nuevo() -> None:
    filas = [
        [26, "2026-09-26", "Paula Rios", "Medellin", "Tecnologia", "Servidor", 1, 4500000],
        [27, "2026-09-27", "Mateo Leon", "Bogota", "Oficina", "Mesa", 2, 530000],
        [28, "2026-09-28", "Lucia Gil", "Cali", "Tecnologia", "Consola", 1, 1900000],
        [29, "2026-09-29", "Samuel Arias", "Medellin", "Accesorios", "Webcam", 3, 210000],
        [30, "2026-09-30", "Natalia Soto", "Bogota", "Tecnologia", "Impresora 3D", 1, 2800000],
    ]
    with NEW_DATA_FILE.open("w", newline="", encoding="utf-8") as archivo:
        escritor = csv.writer(archivo)
        escritor.writerow(["id_venta", "fecha", "cliente", "ciudad", "categoria", "producto", "cantidad", "precio"])
        escritor.writerows(filas)


def main() -> None:
    estado = cargar()
    athena = boto3.client("athena", region_name=estado["region"])
    glue = boto3.client("glue", region_name=estado["region"])
    s3 = boto3.client("s3", region_name=estado["region"])
    tabla = f'"{estado["database_name"]}"."{estado["table_name"]}"'

    print("[1/6] Contando los registros iniciales...")
    query_id = ejecutar_athena(athena, estado, f"SELECT COUNT(*) AS total_inicial FROM {tabla}")
    mostrar(athena, query_id)

    print("\n[2/6] FILTRO: ventas con precio igual o superior a $500.000")
    query_id = ejecutar_athena(
        athena,
        estado,
        f"SELECT id_venta, producto, ciudad, cantidad, precio, cantidad * precio AS total "
        f"FROM {tabla} WHERE precio >= 500000 ORDER BY total DESC",
    )
    mostrar(athena, query_id)

    print("\n[3/6] AGRUPACIÓN: ingresos y unidades por ciudad")
    query_id = ejecutar_athena(
        athena,
        estado,
        f"SELECT ciudad, COUNT(*) AS ventas, SUM(cantidad) AS unidades, "
        f"SUM(cantidad * precio) AS ingresos FROM {tabla} "
        f"GROUP BY ciudad ORDER BY ingresos DESC",
    )
    mostrar(athena, query_id)

    print("\n[4/6] Agregando cinco registros nuevos en S3...")
    crear_archivo_nuevo()
    s3.upload_file(
        str(NEW_DATA_FILE), estado["bucket_name"], "entrada/ventas_nuevas.csv"
    )
    print(f"Archivo cargado: s3://{estado['bucket_name']}/entrada/ventas_nuevas.csv")

    print("[5/6] Ejecutando nuevamente el crawler de Glue...")
    glue.start_crawler(Name=estado["crawler_name"])
    esperar_crawler(glue, estado["crawler_name"])

    print("[6/6] Verificando el nuevo total mediante Athena...")
    query_id = ejecutar_athena(athena, estado, f"SELECT COUNT(*) AS total_actualizado FROM {tabla}")
    mostrar(athena, query_id)

    print("\nPRUEBA GLUE Y ATHENA COMPLETADA CORRECTAMENTE")
    print("Registros iniciales esperados: 25")
    print("Registros después de la actualización: 30")


if __name__ == "__main__":
    main()
