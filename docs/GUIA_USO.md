# Guía de uso por rol

Guía breve del Sistema WorkFlow de matrícula para el staff. Cada sección describe qué hace un
rol y los pasos típicos de un día de matrícula.

## Antes de empezar

- **Ingreso:** con su correo institucional y la contraseña temporal que le entregó el
  Administrador. En el primer ingreso el sistema le pide cambiarla (mínimo 10 caracteres).
- **Sesión:** se cierra sola tras 30 minutos sin uso, o a las 8 horas. Tras 5 intentos
  fallidos, la cuenta se bloquea 15 minutos.
- **Menú:** cada usuario ve solo las opciones de su rol.
- **Número de atención:** cada postulante recibe uno al iniciar (ej.: `M-0042`). Es la forma
  de llamarlo en la fila; aparece en la bandeja, en la ficha, en su celular y en su correo.

### Cómo avanza un postulante

1. **Inicio:** el postulante escanea el QR «Inicia aquí» o lo inicia el staff. Recibe su
   número y un enlace de seguimiento por correo.
2. **Fases de consulta** (si tiene dudas): Becas, Finanzas u otras, en el orden de su ruta.
3. **MNP:** completa la matrícula en la plataforma, asistido en un tótem. Queda **Matriculado**.

Si el caso no puede terminar en MNP (ej.: pago en efectivo), se deriva a un **flujo
alternativo**: una secuencia fija de fases que termina con un **cierre manual**.

En su celular, el postulante ve a qué fase dirigirse. La página se actualiza sola cada 10
segundos.

---

## Responsable de «Iniciado»

Recibe a los postulantes y define su ruta.

**Iniciar el proceso de un postulante** (menú **Iniciar proceso**):
1. Ingrese el RUT.
2. Si ya está registrado, revise sus datos y corrija lo necesario. El RUT no se puede
   cambiar. Si es nuevo, complete el formulario.
3. Responda **«¿El postulante tiene dudas?»**:
   - **No:** va directo a MNP.
   - **Sí:** numere 1, 2, 3… las fases de consulta que debe visitar, en ese orden.
   - **Flujo alternativo:** si el caso no puede terminar en MNP. Elija el flujo y la fase de
     entrada.
4. **Confirmar.** Indíquele al postulante su número de atención y a qué fase dirigirse.

**Postulantes que iniciaron por QR:** aparecen **en espera** en su bandeja, con las dudas que
marcaron como ruta propuesta. Presione **Atender** y luego **Finalizar y derivar**: confirme o
ajuste la ruta.

Si tiene el permiso de carga manual, también puede usar **Registrar postulante**. Eso solo
registra los datos: no inicia el proceso ni envía correos.

---

## Responsable de una fase de consulta (Becas, Finanzas, etc.)

Su herramienta es la **Bandeja** (menú **Bandeja**). Se actualiza sola cada 10 segundos.

- **En espera:** por orden de llegada, con el tiempo que lleva esperando cada uno.
- **En atención:** los que está atendiendo usted u otro responsable.
- **Próximos:** los que vienen a su fase después de la actual.

**Su módulo:** al comienzo de la jornada, escriba en **Mi módulo** (arriba en la bandeja) el
número del puesto donde atiende y presione **Guardar**. Si cambia de puesto, actualícelo.

**Atender a un postulante:**
1. Presione **Llamar**: la pantalla del hall muestra en grande su número, la fase y su
   módulo (ej.: «M-0016 · Finanzas · Módulo 4») y lo anuncia con un sonido y una voz. Si no
   se presenta, el llamado se repite cada 30 segundos, hasta 3 veces; la fila muestra
   «Llamado 2 de 3» y, después, «No se presentó». Puede **Volver a llamar** o **Cancelar
   llamado** (sigue en espera, en su lugar de la fila).
2. Cuando llegue, presione **Atender**. Así se mide el tiempo de espera y el de atención, y
   en la pantalla pasa a «En atención» con su módulo. Si ya está frente a usted, puede
   presionar **Atender** sin llamarlo.
3. Al terminar, presione **Finalizar y derivar** y elija el resultado: **Completado** o
   **Completado con observaciones** (la observación es obligatoria y el postulante no la ve).
4. En **¿Cómo sigue?**, elija:
   - **Continuar la ruta:** pasa a la siguiente fase. Puede agregar, quitar o reordenar las
     fases de consulta pendientes; MNP siempre queda al final.
   - **Derivar ahora:** lo envía a otra fase de consulta sin terminar su atención. Su fase se
     vuelve a planificar para después.
   - **Derivar a flujo alternativo:** si el caso no puede terminar en MNP.

**Si el postulante se fue de la fila:** use **Abandonó**. Sale de la bandeja y su celular
muestra que la atención quedó en pausa. Si vuelve, se le **reactiva** desde su ficha (lo
puede hacer el responsable de la fase en que abandonó, o el de «Iniciado»).

> Si finaliza sin haber presionado **Atender**, el sistema pide confirmarlo y la atención
> queda «sin tiempo de atención» en los reportes.

### En un flujo alternativo

La secuencia de fases es fija: **Finalizar** lo pasa a la fase siguiente del flujo. En la
última fase, el botón es **Cerrar matrícula manualmente**: escriba el detalle del cierre (ej.:
número de boleta) y confírmelo. El postulante queda Matriculado.

Si el caso cambia, use **Devolver al flujo principal**, con el motivo y la nueva ruta.

### Pantalla del hall de espera

Abra `https://<servidor>/pantalla/` en el televisor del hall (no requiere iniciar sesión: solo
muestra números de atención, fases y módulos, nunca nombres ni RUT).

- Al abrirla, presione **Activar sonido y voz**: los navegadores solo reproducen sonido
  después de un clic. Si el equipo se reinicia, hay que volver a presionarlo. En un equipo
  dedicado puede evitarse abriendo Chrome con
  `--kiosk --autoplay-policy=no-user-gesture-required`.
- La voz usa la que trae el equipo en español; si no tiene ninguna, suena solo la campanilla.
- Si dos responsables de una misma fase llaman a la vez, cada uno aparece con su módulo.

---

## Responsable de MNP

Usa la **Bandeja** como cualquier fase.

1. **Atender** al postulante y acompañarlo en la plataforma MNP.
2. **Finalizar y derivar** → **Completar la matrícula**. El postulante queda **Matriculado**,
   lo ve en su celular y recibe el correo de matrícula completada.

Si en MNP surge una duda, use **Derivar ahora** hacia la fase de consulta. MNP se vuelve a
planificar después de esa fase.

---

## Todos los roles: buscar un postulante

En el menú, **Buscar postulante** por RUT. La ficha muestra:
- Sus datos y carreras de interés.
- Su recorrido: fases completadas, la actual y las siguientes, con tiempos y responsables.
- El historial de derivaciones y los correos enviados.
- **Reenviar enlace:** si no le llegó el correo (máximo 3 reenvíos por hora).

---

## Gestor

Puede hacer todo lo anterior en cualquier fase, y además:

- **Corregir la ruta** de un postulante en curso, con motivo.
- **Reabrir** la última atención completada, si la siguiente aún no empezó (ej.: se finalizó
  por error).
- **Editar datos** de un postulante desde su ficha (no el RUT), o desactivarlo si se registró
  con un RUT equivocado.
- **Carga masiva** de postulantes desde Excel o CSV: descargue la plantilla, súbala, revise la
  vista previa y elija qué hacer con los ya registrados.
- **Reportes:** estado de los postulantes, en espera en tiempo real, tiempos por fase y
  responsable, abandonos, rutas, flujos alternativos, carreras, inicios, productividad,
  cargados sin iniciar y correos fallidos. Todos se descargan en Excel o CSV.
- **Auditoría** (solo lectura): acciones sobre expedientes, postulantes y cargas.

---

## Administrador

Puede hacer todo lo del Gestor, y además configura el sistema:

- **Procesos:** crear el proceso de matrícula (ej.: «Matrícula 2027»), sus fases de consulta
  y sus flujos alternativos; asignar responsables a cada fase; activarlo y cerrarlo.
  - Solo puede haber un proceso activo.
  - Cuando el proceso tiene su primer expediente, ya no se pueden agregar ni quitar fases.
  - Al cerrarlo, quienes no quedaron matriculados pasan a «Proceso pendiente».
- **Usuarios:** crear cuentas (con contraseña temporal), asignar rol y fases, dar el permiso de
  carga manual, desactivar y restablecer contraseñas.
- **Carreras:** el catálogo que se usa en todas las vías de registro.
- **Póster del QR:** en **Procesos**, para imprimir el cartel «Inicia aquí».
- **Auditoría** completa, incluidos los inicios de sesión y los cambios de configuración.

**Fin del período:** los datos personales de cada proceso se eliminan en su fecha de
eliminación (por defecto, el 30 de noviembre). 30 días antes aparece un aviso en el panel de
inicio para exportar los reportes necesarios.

---

## Ambiente de prueba (piloto y capacitación)

En un servidor de prueba, el comando siguiente arma un proceso «Matrícula DEMO» con usuarios
de prueba y 30 postulantes ficticios en distintos estados:

```bash
flask --app app:create_app datos-demo
```

- Muestra los correos y las contraseñas temporales de los usuarios de prueba: un Gestor y los
  responsables de Iniciado, Becas, Finanzas (con Caja y Firma de contrato) y MNP.
- Los postulantes usan correos `@example.com`: ningún correo llega a una persona real.
- Se niega a correr si ya hay un proceso activo o expedientes, para no mezclarse con datos
  reales.
- Para borrarlo al terminar: `flask --app app:create_app datos-demo --eliminar` (los usuarios de
  prueba quedan desactivados).
