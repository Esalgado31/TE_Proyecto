const API = "http://localhost:3001";


// ---------------------------------------------------------
// Obtener el ID del tutor que inició sesión
// ---------------------------------------------------------

const tutorId = localStorage.getItem("usuarioId");


// Si no existe un usuario guardado
if (!tutorId) {

    document.getElementById("cargando").style.display = "none";

    mostrarError("No se encontró la sesión del tutor.");

} else {

    cargarTutor();

}


// ---------------------------------------------------------
// Cargar información del tutor y sus hijos
// ---------------------------------------------------------

async function cargarTutor() {

    try {

        const respuesta = await fetch(
            `${API}/api/tutores/${tutorId}`
        );

        if (!respuesta.ok) {

            const error = await respuesta.json();

            throw new Error(
                error.detail || "No se pudo cargar la información."
            );
        }

        const datos = await respuesta.json();

        mostrarTutor(datos);

    } catch (error) {

        console.error(error);

        mostrarError(
            "No se pudo cargar la información del tutor: " +
            error.message
        );
    }
}


// ---------------------------------------------------------
// Mostrar tutor e hijos
// ---------------------------------------------------------

function mostrarTutor(datos) {

    document.getElementById("cargando").style.display = "none";

    const tutor = datos.tutor;
    const hijos = datos.hijos || [];


    // Nombre del tutor

    document.getElementById("bienvenida").textContent =
        `Bienvenido, ${tutor.nombre} ${tutor.apellido}`;


    const contenedor = document.getElementById("hijos");

    contenedor.innerHTML = "";


    // Si no tiene hijos

    if (hijos.length === 0) {

        contenedor.innerHTML = `
            <div class="sin-hijos">
                <h3>No hay hijos asociados</h3>
                <p>
                    No se encontraron estudiantes asociados
                    a este tutor.
                </p>
            </div>
        `;

        return;
    }


    // Crear tarjeta para cada hijo

    hijos.forEach(hijo => {

        const tarjeta = document.createElement("div");

        tarjeta.className = "tarjeta-hijo";


        let clasesHTML = "";


        // Si no lleva clases

        if (!hijo.clases || hijo.clases.length === 0) {

            clasesHTML = `
                <p class="sin-clases">
                    No hay clases registradas.
                </p>
            `;

        } else {

            hijo.clases.forEach(clase => {

                let nota = "Sin nota";

                if (clase.notaActual !== null &&
                    clase.notaActual !== undefined) {

                    nota = clase.notaActual;
                }


                let estado = "";

                if (clase.notaActual !== null &&
                    clase.notaActual !== undefined) {

                    if (clase.notaActual >= 60) {

                        estado = `
                            <span class="aprobado">
                                Aprobando
                            </span>
                        `;

                    } else {

                        estado = `
                            <span class="reprobado">
                                Reprobando
                            </span>
                        `;
                    }
                }


                let riesgo = "";

                if (clase.riesgo) {

                    riesgo = `
                        <span class="riesgo ${clase.riesgo.nivel.toLowerCase()}">
                            Riesgo ${clase.riesgo.nivel}
                        </span>
                    `;
                }


                clasesHTML += `

                    <div class="clase">

                        <div class="clase-info">

                            <h4>
                                ${clase.nombre || "Clase"}
                            </h4>

                            <p>
                                Docente:
                                ${clase.docente || "No registrado"}
                            </p>

                        </div>


                        <div class="clase-nota">

                            <strong>
                                ${nota}
                            </strong>

                            ${estado}

                            ${riesgo}

                        </div>

                    </div>

                `;
            });
        }


        tarjeta.innerHTML = `

            <div class="hijo-header">

                <div class="avatar">
                    ${hijo.nombre
                        ? hijo.nombre.charAt(0).toUpperCase()
                        : "?"}
                </div>

                <div>

                    <h3>
                        ${hijo.nombre || ""}
                        ${hijo.apellido || ""}
                    </h3>

                    <p>
                        Información académica
                    </p>

                </div>

            </div>


            <h3 class="titulo-clases">
                📚 Clases
            </h3>


            <div class="lista-clases">

                ${clasesHTML}

            </div>

        `;


        contenedor.appendChild(tarjeta);

    });
}


// ---------------------------------------------------------
// Mostrar error
// ---------------------------------------------------------

function mostrarError(mensaje) {

    const error = document.getElementById("error");

    error.textContent = mensaje;

    error.style.display = "block";
}


// ---------------------------------------------------------
// Cerrar sesión
// ---------------------------------------------------------

function cerrarSesion() {

    localStorage.removeItem("usuarioId");
    localStorage.removeItem("rol");
    localStorage.removeItem("nombre");

    window.location.href = "login.html";
}