"""
app.py — Backend del Sistema Predictivo de Rendimiento Académico.

Un solo servidor en Python que hace las dos cosas:
    1. Lee MongoDB Atlas y sirve los datos por HTTP al navegador.
    2. Carga el modelo de machine learning y predice riesgo.

Por qué en Python y no en Node: el modelo es Python. Con Node harían falta DOS
servidores hablándose por HTTP; así el modelo vive en el mismo proceso y
predecir es una llamada a función (~1 ms) en vez de una llamada de red.

El modelo se carga UNA VEZ al arrancar (tarda ~1.7 s). Si se cargara en cada
petición, cada clic pagaría esos 1.7 s.

Arrancar:  cd backend  &&  python -m uvicorn app:app --reload --port 3001
Probar:    http://localhost:3001/docs
"""

import json
import os
import sys
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd
from bson import ObjectId
from bson.errors import InvalidId
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient

RAIZ = Path(__file__).resolve().parent.parent

# El calculo de las variables vive en ml/caracteristicas.py y lo comparten el
# entrenamiento y esta API. Es la unica forma de garantizar que el modelo reciba
# al predecir exactamente los mismos numeros que vio al aprender.
sys.path.insert(0, str(RAIZ / "ml"))
from caracteristicas import (  # noqa: E402
    VARIABLES, TAREAS_VISIBLES, NOTA_APROBACION,
    calcular_variables, nota_parcial_actual,
)

# ---------------------------------------------------------------------------
# Umbrales de riesgo
# ---------------------------------------------------------------------------
# Deliberadamente por debajo del 0.50 que usaria el modelo por defecto.
#
# El modelo tiene precision 1.000 y exhaustividad 0.769: cuando avisa siempre
# acierta, pero se le escapan 3 de cada 13 que reprueban. Para una alerta
# temprana ese desbalance esta al reves. Un falso positivo le cuesta al docente
# una conversacion de mas; un falso negativo le cuesta la clase a un estudiante
# que nadie ayudo. Bajamos el umbral a proposito para atrapar mas casos, aunque
# eso genere algunas alertas de mas.
UMBRAL_ALTO = 0.45
UMBRAL_MEDIO = 0.25

estado = {}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Se ejecuta una vez al arrancar y una vez al apagar."""
    load_dotenv(RAIZ / "backend" / ".env")

    estado["cliente"] = MongoClient(os.environ["MONGODB_URI"])
    estado["db"] = estado["cliente"][os.environ.get("DB_NAME", "AplicativoEducativo")]
    estado["db"].command("ping")
    print(f'Conectado a Mongo — base "{estado["db"].name}"')

    estado["modelo"] = joblib.load(RAIZ / "ml" / "modelo_riesgo.joblib")
    print("Modelo de riesgo cargado (una sola vez, para toda la vida del servidor)")

    yield
    estado["cliente"].close()


app = FastAPI(
    title="Sistema Predictivo de Rendimiento Académico",
    description="Datos académicos desde MongoDB Atlas + predicción de riesgo con el modelo entrenado.",
    version="1.0.0",
    lifespan=lifespan,
)

# El navegador abre los HTML desde file:// o desde otro puerto, así que sin esto
# el fetch() se bloquea por política de mismo origen.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ===========================================================================
# Helpers
# ===========================================================================

def db():
    return estado["db"]


def a_oid(valor: str) -> ObjectId:
    """Convierte texto a ObjectId, o responde 400 en vez de reventar con 500."""
    try:
        return ObjectId(valor)
    except (InvalidId, TypeError):
        raise HTTPException(400, f"'{valor}' no es un ID válido de MongoDB (deben ser 24 caracteres hexadecimales)")


def nombre_completo(doc) -> str:
    if not doc:
        return "—"
    return f"{doc.get('Nombre', '')} {doc.get('Apellido', '')}".strip()


def evaluaciones_de(estudiante_id: ObjectId, clases_filtro=None):
    """Agrupa las calificaciones de un estudiante por clase.

    Devuelve { clase_id: [ {pct, ponderacion, fecha_limite, fecha_entrega,
                            nombre, tipo, observaciones}, ... ] }
    en el formato que espera calcular_variables().
    """
    calificaciones = list(db().Calificaciones.find({"Estudiante": estudiante_id}))
    if not calificaciones:
        return {}

    ids_tareas = list({c["Tarea"] for c in calificaciones})
    tareas = {t["_id"]: t for t in db().Tareas.find({"_id": {"$in": ids_tareas}})}

    por_clase = {}
    for cal in calificaciones:
        tarea = tareas.get(cal["Tarea"])
        if not tarea:
            continue  # calificación huérfana: su tarea ya no existe
        clase_id = tarea["Clase"]
        if clases_filtro is not None and clase_id not in clases_filtro:
            continue

        por_clase.setdefault(clase_id, []).append({
            "pct": cal.get("PorcentajeCalificacion", 0),
            "ponderacion": tarea.get("Ponderacion", 0),
            "fecha_limite": tarea.get("FechaEntrega"),
            "fecha_entrega": cal.get("FechaEntrega"),
            "nombre": tarea.get("Nombre"),
            "tipo": tarea.get("Tipo"),
            "observaciones": cal.get("Observaciones"),
        })

    for lista in por_clase.values():
        lista.sort(key=lambda e: e["fecha_limite"] or datetime.min)
    return por_clase


def nivel_de(probabilidad: float) -> str:
    if probabilidad >= UMBRAL_ALTO:
        return "ALTO"
    if probabilidad >= UMBRAL_MEDIO:
        return "MEDIO"
    return "BAJO"


def explicar(variables: dict, nivel: str) -> list:
    """Describe en palabras los números que recibió el modelo.

    Ojo con qué es esto y qué no: son los datos de ENTRADA descritos en
    castellano, no una explicación de cómo decidió el modelo. Sirve para que el
    docente vea de dónde sale la alerta, no para justificar la probabilidad.

    Recibe el nivel porque hay un caso que hay que manejar con cuidado: cuando
    el modelo marca riesgo pero ninguna señal obvia se dispara. Ahí el Random
    Forest está combinando las cinco variables y encontró un parecido con casos
    que reprobaron, sin que haya un solo dato alarmante. Decir "sin señales de
    alerta" en esa situación contradice al propio modelo, así que se dice lo
    que de verdad está pasando.
    """
    prom = variables["promedio_parcial"]
    tendencia = variables["tendencia"]

    alertas = []
    contexto = []

    # --- Señales que sí son alarmantes por sí solas ---
    if prom < 60:
        alertas.append(f"Promedio parcial de {prom:.0f}, bajo la nota de aprobación")
    elif prom < 70:
        alertas.append(f"Promedio parcial de {prom:.0f}, con poco margen")
    else:
        contexto.append(f"Promedio parcial de {prom:.0f}")

    if tendencia <= -5:
        alertas.append(f"Las notas vienen bajando fuerte ({tendencia:.0f} puntos por evaluación)")
    elif tendencia <= -2:
        alertas.append(f"Las notas vienen bajando ({tendencia:.0f} puntos por evaluación)")
    elif tendencia >= 2:
        contexto.append(f"Las notas vienen subiendo (+{tendencia:.0f} puntos por evaluación)")
    else:
        contexto.append("Notas estables entre evaluaciones")

    if variables["no_entregadas"]:
        n = variables["no_entregadas"]
        alertas.append(f"{n} evaluación{'es' if n > 1 else ''} sin entregar")

    if variables["entregas_tardias"]:
        n = variables["entregas_tardias"]
        alertas.append(f"{n} entrega{'s' if n > 1 else ''} fuera de fecha")

    contexto.append(f"Lleva {variables['puntos_acumulados']:.1f} puntos acumulados del curso")

    # --- Armado final, sin contradecir al modelo ---
    if alertas:
        return alertas + contexto

    if nivel in ("ALTO", "MEDIO"):
        return contexto + [
            "Ninguna señal por separado es alarmante: el modelo llegó a este "
            "resultado combinando las cinco variables, porque el conjunto se "
            "parece al de estudiantes que terminaron reprobando."
        ]

    return ["Sin señales de alerta en las primeras evaluaciones"] + contexto


def predecir_riesgo(evaluaciones: list) -> dict | None:
    """Corre el modelo sobre un curso. None si aún no hay datos suficientes."""
    variables = calcular_variables(evaluaciones)
    if variables is None:
        return None

    # DataFrame con los nombres de columna en el orden del entrenamiento:
    # así sklearn valida que coincidan en vez de aceptarlos en silencio.
    fila = pd.DataFrame([[variables[v] for v in VARIABLES]], columns=VARIABLES)
    probabilidad = float(estado["modelo"].predict_proba(fila)[0][1])
    nivel = nivel_de(probabilidad)

    return {
        "probabilidad": round(probabilidad, 3),
        "porcentaje": round(probabilidad * 100, 1),
        "nivel": nivel,
        "variables": {k: round(v, 2) if isinstance(v, float) else v for k, v in variables.items()},
        "factores": explicar(variables, nivel),
        "evaluacionesVistas": TAREAS_VISIBLES,
        "evaluacionesTotales": len(evaluaciones),
    }


def resumen_de_clase(clase_id: ObjectId, evaluaciones: list) -> dict:
    """Nota actual + riesgo de un estudiante en una clase."""
    nota = nota_parcial_actual(evaluaciones)
    return {
        "notaActual": round(nota, 1) if nota is not None else None,
        "evaluadas": len(evaluaciones),
        "aprobando": (nota >= NOTA_APROBACION) if nota is not None else None,
        "riesgo": predecir_riesgo(evaluaciones),
    }


# ===========================================================================
# Endpoints
# ===========================================================================

@app.get("/api/salud", tags=["Sistema"])
def salud():
    """Comprueba que el servidor, Mongo y el modelo están vivos."""
    return {
        "servidor": "ok",
        "mongo": db().name,
        "modelo": type(estado["modelo"]).__name__,
        "umbrales": {"alto": UMBRAL_ALTO, "medio": UMBRAL_MEDIO},
        # La interfaz usa esto para saber si ofrecer el botón de redacción
        "gemini": bool(os.environ.get("GEMINI_API_KEY", "").strip()),
    }


@app.get("/api/login/{id_usuario}", tags=["Acceso"])
def login(id_usuario: str):
    """Identifica si un ID es de docente, estudiante o tutor."""

    oid = a_oid(id_usuario)

    # ---------------------------------------------------------
    # DOCENTE
    # ---------------------------------------------------------
    docente = db().Docentes.find_one({"_id": oid})

    if docente:
        return {
            "rol": "docente",
            "id": str(oid),
            "nombre": docente.get("Nombre"),
            "apellido": docente.get("Apellido"),
            "destino": "panel-docente.html"
        }

    # ---------------------------------------------------------
    # ESTUDIANTE
    # ---------------------------------------------------------
    estudiante = db().Estudiantes.find_one({"_id": oid})

    if estudiante:
        return {
            "rol": "estudiante",
            "id": str(oid),
            "nombre": estudiante.get("Nombre"),
            "apellido": estudiante.get("Apellido"),
            "destino": "dashboard.html"
        }

    # ---------------------------------------------------------
    # TUTOR
    # ---------------------------------------------------------
    tutor = db().Tutores.find_one({"_id": oid})

    if tutor:
        return {
            "rol": "tutor",
            "id": str(oid),
            "nombre": tutor.get("Nombre"),
            "apellido": tutor.get("Apellido"),
            "destino": "panel-tutor.html"
        }

    raise HTTPException(
        404,
        "Ese ID no corresponde a ningún docente, estudiante ni tutor"
    )

# ===========================================================================
# Tutor
# ===========================================================================

@app.get("/api/tutores/{id_tutor}", tags=["Tutor"])
def hijos_del_tutor(id_tutor: str):
    """
    Devuelve los hijos asociados a un tutor,
    junto con las clases, docentes, notas y riesgo.
    """

    oid_tutor = a_oid(id_tutor)

    # Buscar al tutor
    tutor = db().Tutores.find_one({"_id": oid_tutor})

    if not tutor:
        raise HTTPException(404, "No existe ese tutor")

    # Buscar hijos por el campo Hijos del tutor
    ids_hijos = tutor.get("Hijos", [])

    hijos = []

    if ids_hijos:
        ids_hijos_oid = []

        for hijo_id in ids_hijos:
            try:
                if isinstance(hijo_id, ObjectId):
                    ids_hijos_oid.append(hijo_id)
                else:
                    ids_hijos_oid.append(a_oid(str(hijo_id)))
            except HTTPException:
                continue

        hijos = list(
            db().Estudiantes.find(
                {"_id": {"$in": ids_hijos_oid}}
            )
        )

    # Si no encontró hijos mediante "Hijos",
    # buscar estudiantes que tengan el campo Tutor.
    if not hijos:
        hijos = list(
            db().Estudiantes.find(
                {"Tutor": oid_tutor}
            )
        )

    salida_hijos = []

    for hijo in hijos:

        id_hijo = hijo["_id"]

        # Clases matriculadas
        matriculadas = hijo.get("ClasesMatriculadas", [])

        clases = {
            c["_id"]: c
            for c in db().Clases.find(
                {"_id": {"$in": matriculadas}}
            )
        }

        # Docentes
        ids_docentes = [
            c.get("Docente")
            for c in clases.values()
            if c.get("Docente")
        ]

        docentes = {
            d["_id"]: d
            for d in db().Docentes.find(
                {"_id": {"$in": ids_docentes}}
            )
        }

        # Calificaciones
        por_clase = evaluaciones_de(id_hijo)

        salida_clases = []

        for clase_id in matriculadas:

            clase = clases.get(clase_id)

            if not clase:
                continue

            evaluaciones = por_clase.get(clase_id, [])

            salida_clases.append({
                "id": str(clase_id),
                "nombre": clase.get("NombreClase"),
                "docente": nombre_completo(
                    docentes.get(clase.get("Docente"))
                ),
                **resumen_de_clase(
                    clase_id,
                    evaluaciones
                )
            })

        salida_clases.sort(
            key=lambda c: c["nombre"] or ""
        )

        salida_hijos.append({
            "id": str(id_hijo),
            "nombre": hijo.get("Nombre"),
            "apellido": hijo.get("Apellido"),
            "clases": salida_clases
        })

    return {
        "tutor": {
            "id": str(oid_tutor),
            "nombre": tutor.get("Nombre"),
            "apellido": tutor.get("Apellido")
        },
        "hijos": salida_hijos
    }

@app.get("/api/docentes/{id_docente}", tags=["Docente"])
def clases_del_docente(id_docente: str):
    """Las clases que administra un docente, con promedio y conteo de riesgo."""
    oid = a_oid(id_docente)
    docente = db().Docentes.find_one({"_id": oid})
    if not docente:
        raise HTTPException(404, "No existe ese docente")

    clases = list(db().Clases.find({"Docente": oid}))
    salida = []

    for clase in clases:
        matriculados = list(db().Estudiantes.find({"ClasesMatriculadas": clase["_id"]}))

        notas, en_riesgo = [], 0
        for est in matriculados:
            evaluaciones = evaluaciones_de(est["_id"], {clase["_id"]}).get(clase["_id"], [])
            if not evaluaciones:
                continue
            resumen = resumen_de_clase(clase["_id"], evaluaciones)
            if resumen["notaActual"] is not None:
                notas.append(resumen["notaActual"])
            if resumen["riesgo"] and resumen["riesgo"]["nivel"] in ("ALTO", "MEDIO"):
                en_riesgo += 1

        salida.append({
            "id": str(clase["_id"]),
            "nombre": clase.get("NombreClase"),
            "estudiantes": len(matriculados),
            # Calculado al vuelo, NO leído de Clases.PromedioNota: ese campo se
            # sembró una vez y queda viejo apenas cambia una calificación.
            "promedio": round(sum(notas) / len(notas), 1) if notas else None,
            "enRiesgo": en_riesgo,
        })

    salida.sort(key=lambda c: c["nombre"] or "")
    return {
        "docente": {"id": str(oid), "nombre": docente.get("Nombre"), "apellido": docente.get("Apellido")},
        "clases": salida,
    }


@app.get("/api/clases/{id_clase}/estudiantes", tags=["Docente"])
def estudiantes_de_clase(id_clase: str):
    """Los estudiantes de una clase, cada uno con su nota actual y su riesgo."""
    oid = a_oid(id_clase)
    clase = db().Clases.find_one({"_id": oid})
    if not clase:
        raise HTTPException(404, "No existe esa clase")

    docente = db().Docentes.find_one({"_id": clase.get("Docente")})
    matriculados = list(db().Estudiantes.find({"ClasesMatriculadas": oid}))

    salida = []
    for est in matriculados:
        evaluaciones = evaluaciones_de(est["_id"], {oid}).get(oid, [])
        salida.append({
            "id": str(est["_id"]),
            "nombre": est.get("Nombre"),
            "apellido": est.get("Apellido"),
            **resumen_de_clase(oid, evaluaciones),
        })

    # Los de mayor riesgo primero: es lo que el docente necesita ver arriba.
    salida.sort(key=lambda e: -(e["riesgo"]["probabilidad"] if e["riesgo"] else -1))

    return {
        "clase": {"id": str(oid), "nombre": clase.get("NombreClase"), "docente": nombre_completo(docente)},
        "estudiantes": salida,
    }


@app.get("/api/estudiantes/{id_estudiante}", tags=["Estudiante"])
def clases_del_estudiante(id_estudiante: str):
    """Las clases que lleva un estudiante, con su nota actual y su riesgo."""
    oid = a_oid(id_estudiante)
    estudiante = db().Estudiantes.find_one({"_id": oid})
    if not estudiante:
        raise HTTPException(404, "No existe ese estudiante")

    matriculadas = estudiante.get("ClasesMatriculadas", [])
    clases = {c["_id"]: c for c in db().Clases.find({"_id": {"$in": matriculadas}})}
    docentes = {d["_id"]: d for d in db().Docentes.find({"_id": {"$in": [c.get("Docente") for c in clases.values()]}})}
    por_clase = evaluaciones_de(oid)

    salida = []
    for clase_id in matriculadas:
        clase = clases.get(clase_id)
        if not clase:
            continue
        salida.append({
            "id": str(clase_id),
            "nombre": clase.get("NombreClase"),
            "docente": nombre_completo(docentes.get(clase.get("Docente"))),
            **resumen_de_clase(clase_id, por_clase.get(clase_id, [])),
        })

    salida.sort(key=lambda c: c["notaActual"] if c["notaActual"] is not None else 999)

    return {
        "estudiante": {"id": str(oid), "nombre": estudiante.get("Nombre"), "apellido": estudiante.get("Apellido")},
        "clases": salida,
    }


@app.get("/api/estudiantes/{id_estudiante}/clases/{id_clase}", tags=["Estudiante"])
def detalle_de_clase(id_estudiante: str, id_clase: str):
    """Todas las evaluaciones de un estudiante en una clase, con su riesgo."""
    oid_est, oid_clase = a_oid(id_estudiante), a_oid(id_clase)

    estudiante = db().Estudiantes.find_one({"_id": oid_est})
    if not estudiante:
        raise HTTPException(404, "No existe ese estudiante")
    clase = db().Clases.find_one({"_id": oid_clase})
    if not clase:
        raise HTTPException(404, "No existe esa clase")

    docente = db().Docentes.find_one({"_id": clase.get("Docente")})
    evaluaciones = evaluaciones_de(oid_est, {oid_clase}).get(oid_clase, [])

    tareas = []
    for i, e in enumerate(evaluaciones):
        entregada = not (e["pct"] == 0 and (e["observaciones"] or "").lower().startswith("no entrega"))
        atrasada = bool(e["fecha_entrega"] and e["fecha_limite"] and e["fecha_entrega"] > e["fecha_limite"])
        tareas.append({
            "orden": i + 1,
            "nombre": e["nombre"],
            "tipo": e["tipo"],
            "pesoEnLaNota": e["ponderacion"],
            "porcentajeObtenido": e["pct"],
            "fechaLimite": e["fecha_limite"],
            "fechaEntrega": e["fecha_entrega"],
            "entregada": entregada,
            "atrasada": atrasada,
            "observaciones": e["observaciones"],
            # Marca cuáles de estas evaluaciones alimentaron la predicción
            "vistaPorElModelo": i < TAREAS_VISIBLES,
        })

    return {
        "estudiante": {"id": str(oid_est), "nombre": estudiante.get("Nombre"), "apellido": estudiante.get("Apellido")},
        "clase": {"id": str(oid_clase), "nombre": clase.get("NombreClase"), "docente": nombre_completo(docente)},
        **resumen_de_clase(oid_clase, evaluaciones),
        "tareas": tareas,
    }


# ===========================================================================
# Redacción con IA generativa (Gemini)
# ===========================================================================
# División de trabajo, y es el punto que hay que tener clarísimo:
#
#   El Random Forest DECIDE el riesgo.  Gemini solo lo REDACTA.
#
# Gemini nunca ve la pregunta "¿este estudiante reprueba?". Recibe el veredicto
# ya tomado y los datos que lo produjeron, y su único trabajo es explicarlo en
# lenguaje claro y proponer acciones. Si le dejáramos estimar el riesgo,
# estaríamos tirando el modelo entrenado y presentando la API de Google como si
# fuera nuestro machine learning.

# En orden de preferencia. Los modelos gratuitos devuelven 503 cuando hay pico de
# demanda -- pasa seguido-- asi que hace falta mas de una opcion.
GEMINI_MODELOS = ["gemini-flash-latest", "gemini-3.5-flash", "gemini-flash-lite-latest"]
INTENTOS_POR_MODELO = 2


def _llamar_gemini(prompt: str) -> dict:
    """Llama a Gemini con reintentos y respaldo de modelo. Devuelve JSON parseado."""
    clave = os.environ.get("GEMINI_API_KEY", "").strip()
    if not clave:
        raise HTTPException(503, "No hay GEMINI_API_KEY configurada en backend/.env")

    # Sin "thinkingConfig" a proposito: poner thinkingBudget en 0 ahorraria algo
    # de latencia, pero los modelos "lite" lo rechazan con 400 -- justo los que
    # usamos de respaldo cuando el principal esta saturado. No vale la pena
    # perder el respaldo por unos milisegundos.
    cuerpo = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.4,
        },
    }).encode("utf-8")

    ultimo_error = None
    for modelo in GEMINI_MODELOS:
        for intento in range(INTENTOS_POR_MODELO):
            peticion = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent",
                data=cuerpo,
                headers={"Content-Type": "application/json", "x-goog-api-key": clave},
            )
            try:
                with urllib.request.urlopen(peticion, timeout=30) as resp:
                    datos = json.loads(resp.read())
                texto = datos["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(texto)
            except urllib.error.HTTPError as e:
                detalle = e.read().decode("utf-8", "replace")[:200]
                ultimo_error = f"{e.code} en {modelo}: {detalle}"
                # 400/401/403 son de configuración: ni reintentar ni cambiar de
                # modelo ayuda si el problema es el cuerpo o la clave.
                if e.code in (400, 401, 403):
                    break
                # 503 = saturado. Reintentar y, si sigue, probar el siguiente.
            except Exception as e:
                ultimo_error = f"{type(e).__name__} en {modelo}: {e}"

    raise HTTPException(502, f"Gemini no respondió. {ultimo_error}")


def _prompt_recomendacion(nombre: str, clase: str, prediccion: dict, tareas: list) -> str:
    detalle = "\n".join(
        f"  - {t['nombre']} ({t['tipo']}, vale {t['pesoEnLaNota']}%): "
        + ("no entregada" if not t["entregada"]
           else f"{t['porcentajeObtenido']}%" + (", entregada tarde" if t["atrasada"] else ", a tiempo"))
        for t in tareas if t["vistaPorElModelo"]
    )

    return f"""Sos un orientador académico escribiéndole a un docente sobre uno de sus estudiantes.

Un modelo de machine learning YA determinó el nivel de riesgo. Tu trabajo NO es
evaluar el riesgo, ni cuestionarlo, ni recalcularlo: es explicarlo con claridad y
proponer qué hacer.

ESTUDIANTE: {nombre}
CLASE: {clase}

EVALUACIONES QUE VIO EL MODELO (las 3 primeras del curso):
{detalle}

VARIABLES CALCULADAS:
  - Promedio parcial: {prediccion['variables']['promedio_parcial']}
  - Tendencia: {prediccion['variables']['tendencia']} puntos por evaluación
  - Entregas tardías: {prediccion['variables']['entregas_tardias']}
  - Sin entregar: {prediccion['variables']['no_entregadas']}
  - Puntos acumulados del curso: {prediccion['variables']['puntos_acumulados']}

VEREDICTO DEL MODELO: riesgo {prediccion['nivel']}, {prediccion['porcentaje']}% de
probabilidad de reprobar el curso.

Respondé SOLO con un objeto JSON con esta forma exacta:
{{"resumen": "...", "acciones": ["...", "...", "..."]}}

Reglas:
- "resumen": 2 o 3 oraciones dirigidas al docente, explicando la situación.
- "acciones": exactamente 3 acciones concretas y realizables por el docente.
- No inventes datos, notas ni temas que no estén arriba. No sabés qué contenidos
  cubre la clase, así que no menciones temas específicos.
- No cambies ni pongas en duda el nivel de riesgo.
- Si el riesgo es BAJO, el tono debe ser de seguimiento, no de alarma.
- Español neutro, sin tecnicismos de machine learning.
- No menciones que sos una inteligencia artificial."""


@app.get("/api/recomendacion/{id_estudiante}/{id_clase}", tags=["Machine Learning"])
def recomendacion(id_estudiante: str, id_clase: str):
    """Predicción del modelo + explicación redactada por Gemini.

    El modelo decide el riesgo; Gemini únicamente lo pone en palabras.
    """
    detalle = detalle_de_clase(id_estudiante, id_clase)
    prediccion = detalle.get("riesgo")

    if prediccion is None:
        raise HTTPException(409, f"El modelo necesita al menos {TAREAS_VISIBLES} evaluaciones")

    nombre = f"{detalle['estudiante']['nombre']} {detalle['estudiante']['apellido']}"
    redaccion = _llamar_gemini(
        _prompt_recomendacion(nombre, detalle["clase"]["nombre"], prediccion, detalle["tareas"])
    )

    return {
        "estudiante": detalle["estudiante"],
        "clase": detalle["clase"],
        "riesgo": prediccion,          # lo decidió el Random Forest
        "redaccion": redaccion,        # lo escribió Gemini
        "generadaPor": "gemini",
    }


@app.get("/api/riesgo/{id_estudiante}/{id_clase}", tags=["Machine Learning"])
def riesgo(id_estudiante: str, id_clase: str):
    """Predicción del modelo para un estudiante en una clase.

    Ésta es la llamada al machine learning propiamente dicho: toma las 3
    primeras evaluaciones, calcula las 5 variables y se las pasa al Random
    Forest entrenado.
    """
    oid_est, oid_clase = a_oid(id_estudiante), a_oid(id_clase)
    evaluaciones = evaluaciones_de(oid_est, {oid_clase}).get(oid_clase, [])

    if not evaluaciones:
        raise HTTPException(404, "Ese estudiante no tiene calificaciones en esa clase")

    prediccion = predecir_riesgo(evaluaciones)
    if prediccion is None:
        raise HTTPException(
            409,
            f"Todavía no hay suficientes evaluaciones: el modelo necesita {TAREAS_VISIBLES} "
            f"y hay {len(evaluaciones)}",
        )
    return prediccion
