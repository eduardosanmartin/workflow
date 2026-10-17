// Actualiza la bandeja cada 10 segundos (RF-17). La cabecera X-Sondeo evita que la
// actualización automática cuente como actividad de la sesión (RF-22).
(function () {
  "use strict";
  var contenedor = document.getElementById("listas");
  var estado = document.getElementById("estado-sondeo");
  if (!contenedor || !window.fetch) return;
  var url = contenedor.getAttribute("data-sondeo");

  function actualizar() {
    if (document.hidden) return;
    // No reemplazar el contenido mientras el usuario está sobre un botón o enlace de la lista
    if (contenedor.contains(document.activeElement)) return;
    fetch(url, { headers: { "X-Sondeo": "1" }, credentials: "same-origin", cache: "no-store" })
      .then(function (respuesta) {
        if (respuesta.redirected || !respuesta.ok) {
          window.location.reload();  // sesión vencida o proceso cerrado: la página completa lo explica
          return null;
        }
        return respuesta.text();
      })
      .then(function (html) {
        if (html == null) return;
        contenedor.innerHTML = html;
        estado.textContent = "Se actualiza automáticamente cada 10 segundos. Última actualización: " +
          new Date().toLocaleTimeString("es-CL", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) + ".";
      })
      .catch(function () {
        estado.textContent = "Sin conexión. Se reintentará en 10 segundos.";
      });
  }

  setInterval(actualizar, 10000);
})();
