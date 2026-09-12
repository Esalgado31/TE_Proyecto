/**
 * login.js — Acceso por ID.
 *
 * Manda el ID a /api/login, que responde si pertenece a un docente o a un
 * estudiante, y redirige a la pantalla que corresponde.
 *
 * Los usuarios de prueba de abajo son los únicos datos fijos que quedan en la
 * interfaz, y son IDs reales de la base: sirven para no tener que copiar y
 * pegar un ObjectId de 24 caracteres cada vez que se prueba algo. Todo lo que
 * se muestra después de entrar viene de la base de datos.
 */

const USUARIOS_DE_PRUEBA = {

  docentes: [
    { id: "6a9cd740f89babdc05b2cfa8", nombre: "Luis Flores", desc: "Programación I · Bases de Datos" },
    { id: "6a9cd740f89babdc05b2cfa7", nombre: "Ana Castellanos", desc: "Literatura Española · Redacción" },
    { id: "6a9cd740f89babdc05b2cfa9", nombre: "Elena Martínez", desc: "Psicología General · Sociología" },
  ],

  estudiantes: [
    { id: "6a9cdb16f89babdc05b2cfea", nombre: "David Rivas", desc: "3 clases · en dificultades" },
    { id: "6a9cdb16f89babdc05b2d014", nombre: "Omar Quintanilla", desc: "4 clases · rendimiento mixto" },
    { id: "6a9cdb16f89babdc05b2cfda", nombre: "Luis Hernandez", desc: "4 clases · buen rendimiento" },
  ],

   tutores: [
    { id: "6aa5c5e89b9862a44b74670a", nombre: "Carlos Rivas", desc: "Tutor de David Rivas" },
  ],


};

document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("form-acceso");
  const input = document.getElementById("input-id");
  const boton = document.getElementById("btn-entrar");
  const aviso = document.getElementById("aviso");

  function mostrarAviso(mensaje, tipo) {
    aviso.innerHTML = `<div class="aviso ${tipo}">${escapar(mensaje)}</div>`;
  }

  function limpiarAviso() {
    aviso.innerHTML = "";
  }

  async function entrar(id) {
    limpiarAviso();
    boton.disabled = true;
    boton.textContent = "Entrando...";

    try {
      const usuario = await API.login(id.trim());
      API.guardarSesion(usuario);
      mostrarAviso(`Bienvenido, ${usuario.nombre} ${usuario.apellido} (${usuario.rol}).`, "ok");
      window.location.href = usuario.destino;
    } catch (e) {
      mostrarAviso(e.message, "error");
      boton.disabled = false;
      boton.textContent = "Entrar";
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const id = input.value.trim();
    if (!id) {
      mostrarAviso("Escribí un ID, o elegí uno de los usuarios de prueba.", "error");
      return;
    }
    entrar(id);
  });

  // --- Botones de acceso rápido ---
  function pintarUsuarios(contenedor, usuarios, rol) {
    contenedor.innerHTML = usuarios.map(u => `
      <button type="button" class="usuario ${rol}" data-id="${u.id}">
        <span class="ini">${escapar(u.nombre.split(" ").map(p => p[0]).join("").slice(0, 2))}</span>
        <span class="datos">
          <span class="nom">${escapar(u.nombre)}</span>
          <span class="desc">${escapar(u.desc)}</span>
        </span>
        <i class="ph ph-arrow-right" aria-hidden="true"></i>
      </button>
    `).join("");

    contenedor.querySelectorAll(".usuario").forEach(b => {
      b.addEventListener("click", () => {
        input.value = b.dataset.id;
        entrar(b.dataset.id);
      });
    });
  }

  pintarUsuarios(document.getElementById("lista-docentes"), USUARIOS_DE_PRUEBA.docentes, "docente");
  pintarUsuarios(document.getElementById("lista-estudiantes"), USUARIOS_DE_PRUEBA.estudiantes, "estudiante");
  pintarUsuarios(document.getElementById("lista-tutores"), USUARIOS_DE_PRUEBA.tutores, "tutor");
  // --- Estado del servidor: avisa antes de que el usuario intente entrar ---
  const punto = document.getElementById("punto-servidor");
  const texto = document.getElementById("texto-servidor");

  API.salud()
    .then(s => {
      punto.classList.add("vivo");
      texto.textContent = `Servidor conectado · base "${s.mongo}" · modelo ${s.modelo}`;
    })
    .catch(() => {
      punto.classList.add("muerto");
      texto.textContent = `Servidor apagado en ${API.base()} — arrancalo con: cd backend && python -m uvicorn app:app --port 3001`;
    });
});