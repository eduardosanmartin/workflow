# Diagramas BPMN del Sistema WorkFlow Matrícula

Diagramas BPMN 2.0 de la operación completa del sistema (SPEC v0.8). Se abren y editan en
**Camunda Modeler** (Archivo → Abrir) o en cualquier editor BPMN 2.0 (bpmn.io, Bizagi, etc.).
Cada `.bpmn` tiene al lado una imagen `.png` de vista previa.

Para compartir, la carpeta `pdf/` tiene cada diagrama en PDF vectorial (A3 horizontal), con una
segunda hoja que narra el proceso paso a paso (propósito, participantes, pasos y reglas), y
`diagramas-bpmn-workflow.pdf`, que reúne los seis con una portada.

Son diagramas **descriptivos**, no ejecutables (`isExecutable="false"`): documentan cómo opera el
sistema. No están pensados para desplegarse en un motor Camunda.

| Archivo | Qué muestra |
|---|---|
| `01-recorrido-general.bpmn` | **Visión general.** El recorrido completo del postulante: inicio por QR o por staff, fases de consulta, MNP (cierre digital) o flujo alternativo (cierre manual), hasta quedar Matriculado. |
| `02-inicio-por-qr.bpmn` | Autoinicio por QR: proceso activo, CAPTCHA, RUT existente o nuevo, datos enmascarados, reenvío del enlace si ya tiene expediente. |
| `03-inicio-por-staff.bpmn` | Inicio por el responsable de «Iniciado»: revisión de datos y la pregunta «¿Tiene dudas?» (ruta directa, con consultas o flujo alternativo). |
| `04-atencion-en-una-fase.bpmn` | El trabajo en la bandeja de cualquier fase: atender, finalizar, continuar la ruta, derivar ahora, derivar a flujo alternativo, completar la matrícula, y el abandono con su reactivación. |
| `05-flujo-alternativo.bpmn` | Flujo alternativo con secuencia fija, cierre manual con detalle obligatorio o devolución al flujo principal. |
| `06-ciclo-del-proceso.bpmn` | Ciclo de vida del proceso de matrícula: configuración, carga de postulantes, activación, operación, cierre y eliminación de datos al fin del período. |

## Cómo se relacionan

- El **01** usa *actividades de llamada* (call activities) para las partes detalladas: la atención
  en una fase (04) y el flujo alternativo (05). En Camunda Modeler, el campo «Called element» de
  cada una apunta al proceso correspondiente (`Proceso_Atencion`, `Proceso_FlujoAlternativo`).
- El **06** llama al **01** («Recorridos de los postulantes»), que se repite por cada postulante
  mientras el proceso está activo.
- Los carriles (lanes) indican quién realiza cada paso: el postulante, los responsables de cada
  fase, el Gestor o Administrador, y el sistema (tareas automáticas, con el ícono de engranaje).

## Verificación

Los seis archivos se abrieron con bpmn-js (el motor de dibujo de Camunda Modeler) sin
advertencias, y pasan las reglas recomendadas de **bpmnlint**, el validador de Camunda, sin
errores ni avisos.
