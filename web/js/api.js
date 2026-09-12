/**
 * api.js — Único punto de contacto entre la interfaz y el backend.
 *
 * Todas las pantallas piden los datos por acá. Si mañana cambia el puerto o
 * la forma de una respuesta, se corrige en este archivo y no en cinco.
 *
 * La "sesión" es deliberadamente mínima: guardamos el ID y el rol que devolvió
 * /api/login en sessionStorage. No hay contraseñas ni tokens porque el proyecto
 * demuestra el modelo predictivo, y un sistema de autenticación real no
 * aportaría nada a esa demostración. En un sistema de producción esto NO sería
 * suficiente: cualquiera podría escribir el ID de otro y ver sus notas.
 */

const API = (() => {
  const BASE_POR_DEFECTO = "http://localhost:3001";

  function base() {
    return localStorage.getItem("backend_url") || BASE_POR_DEFECTO;
  }

  function fijarBase(url) {
    localStorage.setItem("backend_url", url.replace(/\/$/, ""));
  }

  async function pedir(ruta) {
    let resp;
    try {
      resp = await fetch(base() + ruta);
    } catch {
      throw new Error(
        `No se pudo contactar al servidor en ${base()}. ` +
        `¿Está corriendo? Arrancalo con:  cd backend && python -m uvicorn app:app --port 3001`
      );
    }

    if (!resp.ok) {
      let detalle = `Error ${resp.status}`;
      try {
        const cuerpo = await resp.json();
        if (cuerpo.detail) detalle = cuerpo.detail;
      } catch { /* la respuesta no era JSON; nos quedamos con el código */ }
      throw new Error(detalle);
    }
    return resp.json();
  }

  // ---------------- Sesión ----------------

  const SESION = "sesion_usuario";

  function guardarSesion(usuario) {
    sessionStorage.setItem(SESION, JSON.stringify(usuario));
  }

  function sesion() {
    try {
      return JSON.parse(sessionStorage.getItem(SESION));
    } catch {
      return null;
    }
  }

  function cerrarSesion() {
    sessionStorage.removeItem(SESION);
  }

  /**
   * Manda al login si no hay sesión, o si el rol no es el que esta pantalla
   * espera. Devuelve la sesión cuando todo está bien.
   */
  function exigirSesion(rolEsperado) {
    const usuario = sesion();
    if (!usuario) {
      window.location.href = "login.html";
      return null;
    }
    if (rolEsperado && usuario.rol !== rolEsperado) {
      window.location.href = usuario.rol === "docente" ? "panel-docente.html" : "dashboard.html";
      return null;
    }
    return usuario;
  }

  // ---------------- Endpoints ----------------

  return {
    base, fijarBase,
    guardarSesion, sesion, cerrarSesion, exigirSesion,

    login: (id) => pedir(`/api/login/${id}`),
    salud: () => pedir("/api/salud"),

    clasesDelDocente: (id) => pedir(`/api/docentes/${id}`),
    estudiantesDeClase: (idClase) => pedir(`/api/clases/${idClase}/estudiantes`),

    clasesDelEstudiante: (id) => pedir(`/api/estudiantes/${id}`),
    detalleDeClase: (idEst, idClase) => pedir(`/api/estudiantes/${idEst}/clases/${idClase}`),
    tutor: (id) => pedir(`/api/tutores/${id}`),

    riesgo: (idEst, idClase) => pedir(`/api/riesgo/${idEst}/${idClase}`),

    // El modelo decide el riesgo; esto solo pide que Gemini lo redacte.
    recomendacion: (idEst, idClase) => pedir(`/api/recomendacion/${idEst}/${idClase}`),
  };
})();

// ---------------- Utilidades de presentación ----------------

/** Convierte el nivel del modelo en la clase de badge del diseño. */
function claseDeRiesgo(nivel) {
  return { ALTO: "badge-error", MEDIO: "badge-warning", BAJO: "badge-success" }[nivel] || "badge-info";
}

/** Verde / ámbar / rojo para las barras de progreso, según la nota. */
function claseDeNota(nota) {
  if (nota === null || nota === undefined) return "";
  if (nota >= 80) return "success";
  if (nota >= 60) return "warning";
  return "error";
}

function escapar(texto) {
  const d = document.createElement("div");
  d.textContent = texto ?? "";
  return d.innerHTML;
}

function formatearFecha(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("es-HN", { day: "numeric", month: "short", year: "numeric" });
}
