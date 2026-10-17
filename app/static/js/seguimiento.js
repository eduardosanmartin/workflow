// Actualiza la vista de seguimiento del postulante cada 10 segundos (RF-20). Envía la firma
// del estado que ya muestra: si nada cambió, el servidor responde 304 sin contenido (RNF-04).
(function () {
  "use strict";
  var contenedor = document.getElementById("seguimiento");
  if (!contenedor || !window.fetch) return;
  var url = contenedor.getAttribute("data-sondeo");
  var firma = contenedor.getAttribute("data-firma") || "";
  var estado = document.getElementById("estado-sondeo");
  var anuncio = document.getElementById("anuncio");
  var temporizador = null;

  function textoAnuncio() {
    var marca = contenedor.querySelector("[data-anuncio]");
    return marca ? marca.getAttribute("data-anuncio") : "";
  }

  function avisar(texto) {
    if (estado) estado.textContent = texto;
  }

  function detener() {
    clearInterval(temporizador);
    temporizador = null;
  }

  function actualizar() {
    if (document.hidden) return;
    fetch(url, { headers: { "If-None-Match": '"' + firma + '"' }, cache: "no-store", credentials: "omit" })
      .then(function (respuesta) {
        if (respuesta.status === 304) return null;
        if (!respuesta.ok) {
          window.location.reload();  // enlace inválido u otro error: la página completa lo explica
          return null;
        }
        firma = (respuesta.headers.get("ETag") || "").replace(/^W\//, "").replace(/"/g, "");
        return respuesta.text();
      })
      .then(function (html) {
        avisar("Esta página se actualiza sola cada 10 segundos. Última actualización: " +
          new Date().toLocaleTimeString("es-CL", { hour: "2-digit", minute: "2-digit" }) + ".");
        if (html == null) return;
        var antes = textoAnuncio();
        contenedor.innerHTML = html;
        var ahora = textoAnuncio();
        if (ahora && ahora !== antes) {
          anuncio.textContent = ahora;
          contenedor.classList.remove("recien-cambiado");
          void contenedor.offsetWidth;  // reinicia la animación de resalte
          contenedor.classList.add("recien-cambiado");
          if (navigator.vibrate) navigator.vibrate(200);
        }
        if (contenedor.querySelector("[data-finalizado]")) {
          detener();
          if (estado) estado.remove();
        }
      })
      .catch(function () {
        avisar("Sin conexión. Se reintentará en 10 segundos.");
      });
  }

  if (contenedor.querySelector("[data-finalizado]")) return;
  temporizador = setInterval(actualizar, 10000);
  // Al volver a la pestaña o desbloquear el teléfono, actualiza de inmediato
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && temporizador) actualizar();
  });
})();
