from __future__ import annotations

import json
import time
from pathlib import Path

import boto3


BASE_DIR = Path(__file__).resolve().parents[1]
STATE_FILE = BASE_DIR / "infraestructura" / "estado_redshift.json"
CLIENTES_FILE = BASE_DIR / "datos" / "clientes.csv"
PEDIDOS_FILE = BASE_DIR / "datos" / "pedidos.csv"


def cargar_estado() -> dict:
    if not STATE_FILE.exists():
        raise FileNotFoundError("Primero crea la infraestructura del punto 5.")
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def esperar(data_api, statement_id: str) -> dict:
    while True:
        detalle = data_api.describe_statement(Id=statement_id)
        if detalle["Status"] == "FINISHED":
            return detalle
        if detalle["Status"] in {"FAILED", "ABORTED"}:
            raise RuntimeError(detalle.get("Error", "La consulta falló"))
        time.sleep(2)


def ejecutar(data_api, estado: dict, sql: str, nombre: str) -> tuple[str, dict]:
    respuesta = data_api.execute_statement(
        WorkgroupName=estado["workgroup_name"],
        Database=estado["database"],
        SecretArn=estado["secret_arn"],
        Sql=sql,
        StatementName=nombre,
    )
    statement_id = respuesta["Id"]
    detalle = esperar(data_api, statement_id)
    print(f"{nombre}: {detalle['Status']}")
    return statement_id, detalle


def valor(campo: dict):
    if campo.get("isNull"):
        return None
    for clave in ("stringValue", "longValue", "doubleValue", "booleanValue"):
        if clave in campo:
            return campo[clave]
    return str(campo)


def mostrar_resultado(data_api, statement_id: str) -> None:
    resultado = data_api.get_statement_result(Id=statement_id)
    columnas = [item["name"] for item in resultado["ColumnMetadata"]]
    print(" | ".join(columnas))
    print("-" * 80)
    for registro in resultado["Records"]:
        print(" | ".join(str(valor(campo)) for campo in registro))


def main() -> None:
    estado = cargar_estado()
    region = estado["region"]
    s3 = boto3.client("s3", region_name=region)
    data_api = boto3.client("redshift-data", region_name=region)

    print("[1/7] Subiendo los CSV a S3 con Boto3...")
    s3.upload_file(str(CLIENTES_FILE), estado["bucket_name"], "datos/clientes.csv")
    s3.upload_file(str(PEDIDOS_FILE), estado["bucket_name"], "datos/pedidos.csv")
    for objeto in s3.list_objects_v2(Bucket=estado["bucket_name"]).get("Contents", []):
        print(f"s3://{estado['bucket_name']}/{objeto['Key']} ({objeto['Size']} bytes)")

    print("\n[2/7] Creando las tablas mediante Redshift Data API...")
    sentencias = [
        ("DROP TABLE IF EXISTS pedidos", "eliminar_pedidos_anteriores"),
        ("DROP TABLE IF EXISTS clientes", "eliminar_clientes_anteriores"),
        (
            """CREATE TABLE clientes (
                id_cliente INTEGER PRIMARY KEY,
                nombre VARCHAR(100),
                ciudad VARCHAR(60),
                fecha_registro DATE
            )""",
            "crear_tabla_clientes",
        ),
        (
            """CREATE TABLE pedidos (
                id_pedido INTEGER PRIMARY KEY,
                id_cliente INTEGER,
                fecha DATE,
                producto VARCHAR(100),
                valor DECIMAL(12,2)
            )""",
            "crear_tabla_pedidos",
        ),
    ]
    for sql, nombre in sentencias:
        ejecutar(data_api, estado, sql, nombre)

    bucket = estado["bucket_name"]
    role_arn = estado["role_arn"]
    print("\n[3/7] Ejecutando COPY desde S3...")
    copiar_clientes = f"""
        COPY clientes
        FROM 's3://{bucket}/datos/clientes.csv'
        IAM_ROLE '{role_arn}'
        REGION '{region}'
        FORMAT AS CSV
        IGNOREHEADER 1
        DATEFORMAT 'auto'
    """
    copiar_pedidos = f"""
        COPY pedidos
        FROM 's3://{bucket}/datos/pedidos.csv'
        IAM_ROLE '{role_arn}'
        REGION '{region}'
        FORMAT AS CSV
        IGNOREHEADER 1
        DATEFORMAT 'auto'
    """
    ejecutar(data_api, estado, copiar_clientes, "copy_clientes")
    ejecutar(data_api, estado, copiar_pedidos, "copy_pedidos")

    print("\n[4/7] Verificando cantidades cargadas...")
    statement_id, _ = ejecutar(
        data_api,
        estado,
        "SELECT (SELECT COUNT(*) FROM clientes) AS clientes, "
        "(SELECT COUNT(*) FROM pedidos) AS pedidos",
        "conteo_registros",
    )
    mostrar_resultado(data_api, statement_id)

    print("\n[5/7] FILTRO: pedidos con valor superior a $500.000")
    statement_id, _ = ejecutar(
        data_api,
        estado,
        "SELECT id_pedido, producto, valor FROM pedidos "
        "WHERE valor > 500000 ORDER BY valor DESC",
        "consulta_filtro",
    )
    mostrar_resultado(data_api, statement_id)

    print("\n[6/7] UNIÓN: quién compró qué y desde qué ciudad")
    statement_id, _ = ejecutar(
        data_api,
        estado,
        "SELECT c.nombre, c.ciudad, p.producto, p.valor "
        "FROM clientes c JOIN pedidos p ON c.id_cliente = p.id_cliente "
        "ORDER BY p.valor DESC LIMIT 10",
        "consulta_union",
    )
    mostrar_resultado(data_api, statement_id)

    print("\n[7/7] AGREGACIÓN: ingresos por ciudad")
    statement_id, _ = ejecutar(
        data_api,
        estado,
        "SELECT c.ciudad, COUNT(*) AS pedidos, SUM(p.valor) AS ingresos "
        "FROM clientes c JOIN pedidos p ON c.id_cliente = p.id_cliente "
        "GROUP BY c.ciudad ORDER BY ingresos DESC",
        "consulta_agregacion",
    )
    mostrar_resultado(data_api, statement_id)

    print("\nPRUEBA REDSHIFT COMPLETADA CORRECTAMENTE")
    print("La contraseña fue administrada por Secrets Manager y no está en el código.")


if __name__ == "__main__":
    main()
