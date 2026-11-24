// Pantalla del hall: consulta el estado cada 3 s, muestra el llamado en grande y lo anuncia
// con un sonido y una voz. El servidor decide cuándo repetir un llamado (cada 30 s mientras
// el postulante no se presenta); cada aviso trae una clave y aquí se anuncia una sola vez.
(function () {
  "use strict";

  var SONDEO_MS = 3000;
  var AVISO_RECIENTE_S = 20;  // al recargar, no anunciar avisos viejos

  var raiz = document.getElementById("pantalla");
  var url = raiz.getAttribute("data-estado");
  var elActual = document.getElementById("actual");
  var elOtros = document.getElementById("otros");
  var elAtendiendo = document.getElementById("atendiendo");
  var elSinAtendiendo = document.getElementById("sin-atendiendo");
  var elProceso = document.getElementById("proceso");
  var elConexion = document.getElementById("conexion");
  var elReloj = document.getElementById("reloj");
  var capaActivar = document.getElementById("activar");

  var conSonido = false;
  var audio = null;
  var anunciados = {};  // clave → true
  var cola = [];
  var anunciando = null;  // llamado que se está anunciando: ocupa el lugar grande
  var ultimo = JSON.parse(document.getElementById("datos-iniciales").textContent);

  // ── Dibujo ──

  function el(etiqueta, clase, texto) {
    var nodo = document.createElement(etiqueta);
    if (clase) nodo.className = clase;
    if (texto != null) nodo.textContent = texto;
    return nodo;
  }

  function principal(datos) {
    if (anunciando) {
      // Sigue vigente: se muestra con su aviso actualizado
      for (var i = 0; i < datos.llamando.length; i++) {
        if (datos.llamando[i].clave.split("-")[0] === anunciando.clave.split("-")[0]) return datos.llamando[i];
      }
      return anunciando;
    }
    return datos.llamando[0] || null;
  }

  function ajustarAlAncho(nodo) {
    // Si el número no cabe en una línea, se achica hasta que quepa
    nodo.style.fontSize = "";
    var disponible = elActual.clientWidth * 0.94;
    var tamano = parseFloat(getComputedStyle(nodo).fontSize);
    while (nodo.scrollWidth > disponible && tamano > 24) {
      tamano *= 0.92;
      nodo.style.fontSize = tamano + "px";
    }
  }

  function dibujar(datos) {
    elProceso.textContent = datos.proceso || "";
    var grande = principal(datos);
    var idGrande = grande ? grande.clave.split("-")[0] : null;

    elActual.replaceChildren();
    if (!datos.proceso) {
      elActual.className = "actual vacio-llamado";
      elActual.textContent = "No hay un proceso de matrícula activo";
    } else if (!grande) {
      elActual.className = "actual vacio-llamado";
      elActual.textContent = "Espere su número en pantalla";
    } else {
      elActual.className = "actual" + (elActual.dataset.destello === grande.clave ? " destello" : "");
      var numero = el("div", "numero", grande.numero);
      elActual.appendChild(numero);
      ajustarAlAncho(numero);
      elActual.appendChild(el("div", "fase", grande.fase));
      elActual.appendChild(el("div", "modulo", "Módulo " + grande.modulo));
      if (grande.aviso > 1) {
        elActual.appendChild(el("div", "repeticion", "Llamado " + grande.aviso + " de " + grande.avisos));
      }
    }

    elOtros.replaceChildren();
    datos.llamando.forEach(function (ll) {
      if (ll.clave.split("-")[0] === idGrande) return;
      var li = el("li");
      li.appendChild(el("strong", null, ll.numero));
      li.appendChild(document.createTextNode(ll.fase + " · Módulo " + ll.modulo));
      elOtros.appendChild(li);
    });

    elAtendiendo.replaceChildren();
    datos.atendiendo.forEach(function (a) {
      var tr = el("tr");
      tr.appendChild(el("td", null, a.numero));
      tr.appendChild(el("td", null, a.fase));
      tr.appendChild(el("td", null, a.modulo || "—"));
      elAtendiendo.appendChild(tr);
    });
    elSinAtendiendo.hidden = datos.atendiendo.length > 0;
  }

  // ── Sonido y voz ──

  function campana() {
    // Tres notas suaves (do–mi–sol) con caída exponencial: un aviso agradable, no una alarma
    if (!audio) return 0;
    var t = audio.currentTime + 0.05;
    [523.25, 659.25, 783.99].forEach(function (frecuencia, i) {
      var osc = audio.createOscillator();
      var vol = audio.createGain();
      osc.type = "sine";
      osc.frequency.value = frecuencia;
      var inicio = t + i * 0.28;
      vol.gain.setValueAtTime(0.0001, inicio);
      vol.gain.exponentialRampToValueAtTime(0.35, inicio + 0.02);
      vol.gain.exponentialRampToValueAtTime(0.0001, inicio + 1.1);
      osc.connect(vol).connect(audio.destination);
      osc.start(inicio);
      osc.stop(inicio + 1.2);
    });
    return 1500;
  }

  function vozEspanol() {
    if (!window.speechSynthesis) return null;
    var voces = window.speechSynthesis.getVoices();
    var preferidas = ["es-CL", "es-419", "es-US", "es-MX", "es-ES"];
    for (var i = 0; i < preferidas.length; i++) {
      for (var j = 0; j < voces.length; j++) {
        if (voces[j].lang.replace("_", "-") === preferidas[i]) return voces[j];
      }
    }
    for (var k = 0; k < voces.length; k++) {
      if (voces[k].lang.toLowerCase().indexOf("es") === 0) return voces[k];
    }
    return null;
  }

  function hablar(texto, listo) {
    if (!window.speechSynthesis) { listo(); return; }
    var frase = new SpeechSynthesisUtterance(texto);
    var voz = vozEspanol();
    if (voz) frase.voice = voz;
    frase.lang = voz ? voz.lang : "es-CL";
    frase.rate = 0.9;
    var terminado = false;
    function fin() { if (!terminado) { terminado = true; listo(); } }
    frase.onend = fin;
    frase.onerror = fin;
    setTimeout(fin, 12000);  // algunos navegadores no avisan el fin
    window.speechSynthesis.speak(frase);
  }

  function siguienteAnuncio() {
    if (anunciando || !cola.length) return;
    anunciando = cola.shift();
    elActual.dataset.destello = anunciando.clave;
    dibujar(ultimo);
    var terminar = function () {
      setTimeout(function () {
        anunciando = null;
        dibujar(ultimo);
        siguienteAnuncio();
      }, 1500);
    };
    if (!conSonido) { setTimeout(terminar, 3000); return; }
    setTimeout(function () { hablar(anunciando.voz, terminar); }, campana());
  }

  function encolarNuevos(datos) {
    datos.llamando.slice().reverse().forEach(function (ll) {  // del más antiguo al más reciente
      if (anunciados[ll.clave]) return;
      anunciados[ll.clave] = true;
      if (ll.hace <= AVISO_RECIENTE_S) cola.push(ll);
    });
    siguienteAnuncio();
  }

  // ── Sondeo ──

  function actualizar() {
    fetch(url, { cache: "no-store", credentials: "same-origin" })  // incluye el acceso básico del portero, si lo hay
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (datos) {
        elConexion.hidden = true;
        ultimo = datos;
        dibujar(datos);
        encolarNuevos(datos);
      })
      .catch(function () { elConexion.hidden = false; });
  }

  function reloj() {
    elReloj.textContent = new Date().toLocaleTimeString("es-CL", { hour: "2-digit", minute: "2-digit" });
  }

  function mantenerEncendida() {
    if (navigator.wakeLock && navigator.wakeLock.request) {
      navigator.wakeLock.request("screen").catch(function () {});
    }
  }

  function comenzar(sonido) {
    conSonido = sonido;
    capaActivar.hidden = true;
    if (sonido) {
      var Contexto = window.AudioContext || window.webkitAudioContext;
      if (Contexto) audio = new Contexto();
      if (window.speechSynthesis) {
        window.speechSynthesis.getVoices();  // carga la lista de voces
        hablar("Pantalla de llamados activada.", function () {});
      }
    }
    mantenerEncendida();
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) mantenerEncendida();
    });
    if (document.documentElement.requestFullscreen) {
      document.documentElement.requestFullscreen().catch(function () {});
    }
    actualizar();
    setInterval(actualizar, SONDEO_MS);
  }

  document.getElementById("boton-activar").addEventListener("click", function () { comenzar(true); });
  document.getElementById("boton-sin-sonido").addEventListener("click", function () { comenzar(false); });

  // El sondeo empieza al activar la pantalla: los avisos de los últimos segundos se anuncian
  window.addEventListener("resize", function () { dibujar(ultimo); });
  dibujar(ultimo);
  reloj();
  setInterval(reloj, 15000);
})();
