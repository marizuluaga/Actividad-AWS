from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request


NEPTUNE_URL = "https://localhost:8182/gremlin"


def desenvolver_graphson(valor):
    if isinstance(valor, list):
        return [desenvolver_graphson(item) for item in valor]
    if isinstance(valor, dict):
        if "@type" in valor and "@value" in valor:
            tipo = valor["@type"]
            contenido = valor["@value"]
            if tipo == "g:Map" and isinstance(contenido, list):
                return {
                    str(desenvolver_graphson(contenido[i])): desenvolver_graphson(
                        contenido[i + 1]
                    )
                    for i in range(0, len(contenido), 2)
                }
            return desenvolver_graphson(contenido)
        return {clave: desenvolver_graphson(item) for clave, item in valor.items()}
    return valor


def ejecutar(gremlin: str):
    cuerpo = json.dumps({"gremlin": gremlin}).encode("utf-8")
    solicitud = urllib.request.Request(
        NEPTUNE_URL,
        data=cuerpo,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    contexto = ssl.create_default_context()
    contexto.check_hostname = False
    contexto.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(solicitud, context=contexto, timeout=30) as respuesta:
            payload = json.loads(respuesta.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detalle = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Neptune respondió HTTP {error.code}: {detalle}") from error

    datos = payload.get("result", {}).get("data", [])
    datos = desenvolver_graphson(datos)
    return datos if isinstance(datos, list) else [datos]


def literal(valor: str) -> str:
    return json.dumps(valor, ensure_ascii=False)


def crear_vertice(vertice_id: str, etiqueta: str, **propiedades) -> None:
    consulta = f"g.addV({literal(etiqueta)}).property(T.id,{literal(vertice_id)})"
    for clave, valor in propiedades.items():
        consulta += f".property({literal(clave)},{literal(valor)})"
    ejecutar(consulta + ".iterate()")


def crear_relacion(origen: str, etiqueta: str, destino: str) -> None:
    ejecutar(
        f"g.V({literal(origen)}).addE({literal(etiqueta)})"
        f".to(__.V({literal(destino)})).iterate()"
    )


def main() -> None:
    print("[1/6] Probando la conexión con Amazon Neptune...")
    respuesta = ejecutar("g.V().limit(1).count().toList()")
    print("Conexión exitosa con Amazon Neptune.")
    print(f"Respuesta inicial: {respuesta}")

    print("[2/6] Limpiando datos anteriores del ejercicio...")
    ejecutar("g.V().has('actividad','punto4').drop().iterate()")

    print("[3/6] Creando 10 vértices...")
    vertices = [
        ("persona-ana", "Persona", {"nombre": "Ana"}),
        ("persona-bruno", "Persona", {"nombre": "Bruno"}),
        ("persona-carla", "Persona", {"nombre": "Carla"}),
        ("persona-diego", "Persona", {"nombre": "Diego"}),
        ("persona-elena", "Persona", {"nombre": "Elena"}),
        ("empresa-techco", "Empresa", {"nombre": "TechCo"}),
        ("empresa-datacorp", "Empresa", {"nombre": "DataCorp"}),
        ("empresa-cloudify", "Empresa", {"nombre": "Cloudify"}),
        ("ciudad-medellin", "Ciudad", {"nombre": "Medellín"}),
        ("ciudad-bogota", "Ciudad", {"nombre": "Bogotá"}),
    ]
    for vertice_id, etiqueta, propiedades in vertices:
        crear_vertice(
            vertice_id,
            etiqueta,
            actividad="punto4",
            **propiedades,
        )

    print("[4/6] Creando 19 relaciones del grafo...")
    relaciones = [
        ("persona-ana", "KNOWS", "persona-bruno"),
        ("persona-ana", "KNOWS", "persona-carla"),
        ("persona-bruno", "KNOWS", "persona-diego"),
        ("persona-carla", "KNOWS", "persona-elena"),
        ("persona-diego", "KNOWS", "persona-elena"),
        ("persona-bruno", "KNOWS", "persona-elena"),
        ("persona-ana", "WORKS_AT", "empresa-techco"),
        ("persona-bruno", "WORKS_AT", "empresa-techco"),
        ("persona-carla", "WORKS_AT", "empresa-datacorp"),
        ("persona-diego", "WORKS_AT", "empresa-cloudify"),
        ("persona-elena", "WORKS_AT", "empresa-datacorp"),
        ("persona-ana", "LIVES_IN", "ciudad-medellin"),
        ("persona-bruno", "LIVES_IN", "ciudad-medellin"),
        ("persona-carla", "LIVES_IN", "ciudad-bogota"),
        ("persona-diego", "LIVES_IN", "ciudad-bogota"),
        ("persona-elena", "LIVES_IN", "ciudad-medellin"),
        ("empresa-techco", "LOCATED_IN", "ciudad-medellin"),
        ("empresa-datacorp", "LOCATED_IN", "ciudad-bogota"),
        ("empresa-cloudify", "LOCATED_IN", "ciudad-bogota"),
    ]
    for origen, etiqueta, destino in relaciones:
        crear_relacion(origen, etiqueta, destino)

    total_vertices = ejecutar("g.V().has('actividad','punto4').count().toList()")[0]
    total_relaciones = ejecutar(
        "g.V().has('actividad','punto4').bothE().dedup().count().toList()"
    )[0]
    print(f"Vértices creados: {total_vertices}")
    print(f"Relaciones creadas: {total_relaciones}")

    print("\n[5/6] TRES CONSULTAS GREMLIN QUE RECORREN RELACIONES")
    consulta_1 = ejecutar(
        "g.V('persona-ana').out('KNOWS').values('nombre').order().toList()"
    )
    print(f"1. Personas que conoce Ana: {consulta_1}")

    consulta_2 = ejecutar(
        "g.V('persona-ana').out('KNOWS').out('WORKS_AT')"
        ".dedup().values('nombre').order().toList()"
    )
    print(f"2. Empresas donde trabajan las personas que conoce Ana: {consulta_2}")

    consulta_3 = ejecutar(
        "g.V('empresa-datacorp').in('WORKS_AT')"
        ".values('nombre').order().toList()"
    )
    print(f"3. Personas que trabajan en DataCorp: {consulta_3}")

    print("\n[6/6] VERIFICACIÓN FINAL")
    if total_vertices != 10 or total_relaciones < 15:
        raise RuntimeError("El grafo no cumple el mínimo de 10 vértices y 15 relaciones.")

    print("PRUEBA NEPTUNE COMPLETADA CORRECTAMENTE")
    print(f"Total de vértices: {total_vertices}")
    print(f"Total de relaciones: {total_relaciones}")


if __name__ == "__main__":
    main()
