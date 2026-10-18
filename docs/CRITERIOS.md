# Criterios de aceptación (SPEC §11): verificación

Cada criterio de la §11 con la prueba automática que lo demuestra (`tests/<archivo>::<prueba>`).
Revisión del 17-10-2026: **73 de 73 criterios cubiertos**. Todas las pruebas pasan.

## Permisos

| Criterio | Prueba |
|---|---|
| Un usuario de fase no actúa en una fase no asignada (403), ni por interfaz ni por API | `test_bandeja_pantallas::test_actuar_sobre_una_fase_ajena_es_403`, `test_bandeja::test_solo_actua_el_responsable_de_la_fase_actual` |
| No se guarda un usuario de fase sin fases | `test_configuracion::test_usuario_de_fase_exige_al_menos_una_fase`, `test_admin::test_usuario_de_fase_sin_fases_muestra_error` |
| Sin ser responsable de "Iniciado" no inicia procesos (403) | `test_inicio::test_usuario_de_fase_sin_iniciado_no_puede_iniciar`, `test_inicio_pantallas::test_usuario_de_fase_sin_iniciado_recibe_403` |
| El responsable de "Iniciado" inicia un RUT nuevo sin `puede_registrar_postulantes` | `test_inicio::test_responsable_de_iniciado_inicia_un_rut_nuevo_sin_permiso_de_carga` |
| Un usuario de fase nuevo no tiene permiso de carga manual (403) | `test_configuracion::test_crear_usuario_con_clave_temporal`, `test_postulantes_pantallas::test_menu_segun_rol` |
| Con el permiso, carga manual sí; edición y carga masiva no | `test_postulantes_pantallas::test_usuario_de_fase_con_permiso_solo_registra`, `test_registro::test_usuario_de_fase_no_edita` |
| "Iniciado" corrige datos en el inicio (no el RUT), auditado | `test_inicio::test_staff_corrige_datos_existentes_y_cada_cambio_queda_auditado` |
| El Gestor ve solo la auditoría de expedientes y postulantes, sin modificar | `test_auditoria_consulta::test_gestor_solo_ve_expedientes_postulantes_y_cargas`, `test_auditoria_consulta::test_pantalla_y_permisos` |
| No se activa un proceso con otro ACTIVO | `test_configuracion::test_solo_un_proceso_activo` |

## Carga de datos vs. inicio de proceso

| Criterio | Prueba |
|---|---|
| Carga manual y masiva no crean expedientes ni envían correos | `test_carga_masiva::test_importar_nuevos_sin_iniciar_proceso`, `test_criterios::test_la_carga_manual_no_crea_expedientes_ni_envia_correos` |
| El expediente solo nace por staff o QR, con la vía registrada | `test_inicio::test_numero_de_atencion_correlativo_entre_vias`, `test_ficha::test_inicio_por_qr_en_el_historial` |

## Inicio de proceso

| Criterio | Prueba |
|---|---|
| QR: datos existentes de solo lectura; staff: corregibles salvo RUT; obligatorios vacíos exigidos | `test_inicio::test_qr_solo_completa_pendientes_y_no_cambia_datos_registrados`, `test_inicio::test_qr_exige_los_pendientes_obligatorios`, `test_inicio_pantallas::test_rut_existente_muestra_datos_completos_y_pendientes` |
| Sin proceso ACTIVO ninguna vía inicia (pantallas y API) | `test_inicio::test_sin_proceso_activo_ninguna_via_inicia`, `test_inicio_pantallas::test_sin_proceso_activo_no_muestra_el_formulario_ni_permite_iniciar`, `test_inicio_pantallas::test_qr_sin_proceso_activo` |
| Enmascarado: el HTML no contiene los datos completos | `test_inicio_pantallas::test_qr_enmascarado_no_envia_los_datos_completos` |
| Sin enmascarar: datos completos | `test_inicio_pantallas::test_qr_sin_enmascarar_muestra_datos_completos` |
| RUT con expediente: el QR no crea otro, no muestra el enlace y lo reenvía | `test_inicio_pantallas::test_qr_con_expediente_no_muestra_el_enlace`, `test_inicio::test_qr_no_crea_un_segundo_expediente` |
| Dos inicios simultáneos del mismo RUT: un solo expediente | `test_db::test_dos_inicios_simultaneos_del_mismo_rut_crean_un_solo_expediente` (dos conexiones reales) |

## Recorrido y derivación

| Criterio | Prueba |
|---|---|
| Staff no confirma sin responder "¿Tiene dudas?"; "no" → Iniciado → MNP | `test_inicio_pantallas::test_sin_responder_dudas_no_se_confirma`, `test_inicio::test_staff_rut_nuevo_sin_dudas_va_directo_a_mnp` |
| "Sí": una o más consultas, MNP al final | `test_inicio::test_staff_con_dudas_respeta_el_orden_y_deja_mnp_al_final` |
| QR queda EN_ESPERA en Iniciado, en la bandeja, con ruta propuesta | `test_inicio::test_qr_rut_nuevo_queda_en_espera_en_iniciado_con_ruta_propuesta`, `test_bandeja::test_iniciado_ve_en_espera_a_los_del_qr_con_su_ruta_propuesta` |
| "Iniciado" confirma o cambia la ruta propuesta del QR | `test_bandeja::test_iniciado_confirma_la_ruta_propuesta_del_qr` |
| Derivación visible en el móvil y en la bandeja en ≤ 10 s | `test_seguimiento_sondeo::test_un_cambio_del_recorrido_cambia_la_firma`, `test_bandeja_pantallas::test_parcial_para_el_sondeo` (sondeo cada 10 s) |
| Agregar, quitar o reordenar consultas; MNP siempre al final | `test_bandeja::test_finalizar_con_la_ruta_modificada`, `test_bandeja::test_orden_repetido_o_invalido`, `test_workflow::test_mnp_no_acepta_nueva_ruta` |
| Desde MNP se deriva a una consulta; MNP queda DERIVADA y se replanifica | `test_bandeja::test_derivar_ahora_desde_mnp_y_volver` |
| Nadie actúa sobre una atención que no es la actual (409) | `test_bandeja::test_atender_dos_veces_o_con_version_vieja_responde_409` |
| Nunca más de una atención activa, aun con dos responsables a la vez | `test_db::test_bloqueo_del_expediente_entre_conexiones` (y el índice único `atencion_una_activa`) |
| Se registran los tiempos y aparecen en los reportes | `test_reportes::test_atencion_por_fase_y_responsable` |
| COMPLETADO_CON_OBSERVACIONES sin texto es rechazado | `test_bandeja::test_finalizar_exige_resultado_y_texto_de_observacion` |
| Matriculado si y solo si se completa MNP o la fase final de un flujo | `test_workflow::test_completar_mnp_matricula_con_cierre_digital`, `test_workflow::test_cierre_manual_matricula`, `test_bandeja::test_finalizar_mnp_matricula`, `test_bandeja::test_flujo_alternativo_sigue_su_secuencia_y_cierra_manual` |
| Con observaciones: así en reportes; el postulante ve "Matriculado" | `test_reportes::test_estado_de_los_postulantes`, `test_criterios::test_matriculado_con_observaciones_el_postulante_ve_matriculado` |
| "Abandonó": sale de la bandeja y del reporte de espera; seguimiento en pausa | `test_bandeja::test_el_abandono_sale_de_la_bandeja`, `test_reportes::test_postulantes_en_espera_en_tiempo_real`, `test_criterios::test_abandono_deja_la_atencion_en_pausa_en_el_seguimiento` |
| Reactivación desde la ficha: EN_ESPERA y conserva la ruta | `test_ficha::test_reactivar_en_la_misma_fase`, `test_workflow::test_reactivar_en_la_misma_fase_conserva_la_ruta` |
| Al cerrar, "Abandonó" → "Proceso pendiente" con abandono indicado | `test_reportes::test_estado_al_cierre_muestra_la_fase_detenida` |
| El Gestor reabre la última completada si la siguiente no comenzó; no "Iniciado" | `test_ficha::test_reabrir_la_ultima_completada`, `test_ficha::test_reabrir_dos_veces_no_reabre_otra_atencion` |
| Con expedientes, cambiar la estructura es 409 | `test_configuracion::test_proceso_iniciado_bloquea_la_estructura` |
| Sin expedientes, se agregan, eliminan y reordenan fases | `test_configuracion::test_eliminar_fase`, `test_configuracion::test_reordenar_consultas_sin_mover_las_de_sistema` |
| No se activa sin una fase inicial y una MNP | `test_workflow::test_activacion_exige_una_inicial_y_una_mnp` |

## Flujos alternativos

| Criterio | Prueba |
|---|---|
| Varios flujos; una fase en el principal y en varios flujos | `test_criterios::test_una_fase_puede_estar_en_el_principal_y_en_varios_flujos` |
| Con expedientes, cambiar flujos es 409 | `test_configuracion::test_flujo_con_proceso_iniciado_solo_cambia_nombre_descripcion_y_activo` |
| Derivar a un flujo desde Iniciado, consulta o MNP, eligiendo la entrada | `test_workflow::TestFlujoAlternativo`, `test_bandeja::test_derivar_a_flujo_alternativo` |
| En el flujo, secuencia fija; cambiar la ruta es 409 | `test_bandeja::test_en_flujo_alternativo_no_se_cambia_la_ruta` |
| Devolver al principal con motivo; la ruta termina en MNP | `test_bandeja::test_devolver_al_flujo_principal` |
| La fase final no se finaliza sin detalle del cierre | `test_bandeja::test_flujo_alternativo_sigue_su_secuencia_y_cierra_manual` |
| Cierre manual: Matriculado MANUAL, correo y "Matriculado" | `test_correo::test_correo_de_matricula_por_cierre_manual`, `test_bandeja::test_flujo_alternativo_sigue_su_secuencia_y_cierra_manual` |
| El QR solo ofrece consultas con `mostrar_en_qr` | `test_inicio::test_qr_no_acepta_temas_no_ofrecidos`, `test_inicio_pantallas::test_qr_enmascarado_no_envia_los_datos_completos` |

## Reportería

| Criterio | Prueba |
|---|---|
| Estado: pendientes, matriculados, con observaciones, por tipo de cierre y flujo | `test_reportes::test_estado_de_los_postulantes` |
| En espera: por fase, con el tiempo de cada uno | `test_reportes::test_postulantes_en_espera_en_tiempo_real` |
| Atención por fase: cantidad y tiempos (promedio, mediana, máximo), por fase y responsable | `test_reportes::test_atencion_por_fase_y_responsable` |

## Datos y carga masiva

| Criterio | Prueba |
|---|---|
| RUT con DV inválido rechazado en todas las vías | `test_inicio::test_rut_invalido_es_rechazado`, `test_inicio_pantallas::test_rut_invalido`, `test_registro::test_registro_valida_y_rechaza_rut_existente`, `test_postulantes_pantallas::test_carga_masiva_de_punta_a_punta` |
| Sin correo o sin Carrera 1 no se registra por ninguna vía | `test_inicio::test_staff_datos_invalidos_no_crean_nada`, `test_registro::test_registro_valida_y_rechaza_rut_existente`, `test_criterios::test_sin_correo_o_sin_carrera_1_no_se_registra_por_ninguna_via` |
| Carrera inexistente o inactiva rechazada en la carga masiva, con fila | `test_carga_masiva::test_vista_previa_en_tres_grupos`, `test_carga_masiva::test_carrera_inactiva_se_rechaza` |
| Carrera inactiva fuera de las listas, pero visible en quien la tiene | `test_inicio::test_carrera_inactiva_se_rechaza_para_nuevos_pero_se_conserva_en_existentes`, `test_registro::test_editar_conserva_una_carrera_inactiva` |
| Alias `telefonos_postulante` | `test_carga_masiva::test_columnas_opcionales_y_alias`, `test_registro::test_csv_con_coma_y_alias` |
| Sin columna obligatoria se rechaza todo, indicando cuál | `test_carga_masiva::test_columna_obligatoria_faltante_rechaza_el_archivo` |
| CSV con `;` y con `,` | `test_registro::test_csv_con_punto_y_coma_bom_y_encabezados_libres`, `test_registro::test_csv_con_coma_y_alias` |
| RUT con K y con ceros a la izquierda en Excel | `test_criterios::test_rut_con_k_y_con_ceros_a_la_izquierda_en_excel`, `test_registro::test_excel_lee_todo_como_texto` |
| Filas inválidas: se importan las válidas y se informa cada error | `test_carga_masiva::test_filas_con_error_no_impiden_importar` |
| "No modificar" no altera existentes | `test_carga_masiva::test_politica_no_modificar` |

## Correo y seguimiento

| Criterio | Prueba |
|---|---|
| Al crear el expediente se envía el correo con resumen y enlace, registrado | `test_correo::test_correo_de_inicio_con_resumen_y_enlace`, `test_correo::test_correo_de_inicio_por_qr` |
| Correo de matrícula una única vez (digital o manual) | `test_correo::test_correo_de_matricula_completada_una_sola_vez`, `test_correo::test_correo_de_matricula_por_cierre_manual` |
| Remitente `SMTP_FROM_EMAIL` | `test_correo::test_correo_de_inicio_con_resumen_y_enlace` |
| Si el SMTP falla, el expediente se crea y el envío queda ERROR en la ficha | `test_correo::test_falla_del_smtp_queda_registrada`, `test_correo::test_ficha_muestra_el_error_del_envio` |
| Mismo enlace en el correo y en la redirección del QR | `test_criterios::test_el_enlace_del_correo_y_el_del_qr_son_el_mismo` |
| El seguimiento no muestra datos personales, observaciones ni responsables | `test_inicio::test_vista_de_seguimiento_no_expone_datos_personales`, `test_inicio_pantallas::test_qr_rut_nuevo_redirige_al_seguimiento` |
| Fases completadas, actual y siguiente; derivación en ≤ 10 s | `test_seguimiento_sondeo::test_la_pagina_trae_el_sondeo_y_su_firma`, `test_seguimiento_sondeo::test_un_cambio_del_recorrido_cambia_la_firma` |

## Cierre y conservación de datos

| Criterio | Prueba |
|---|---|
| Al cerrar, no matriculados → "Proceso pendiente", con fase detenida y solo lectura | `test_configuracion::test_cerrar_proceso_deja_pendientes_y_cierra_expedientes`, `test_bandeja::test_proceso_cerrado_no_admite_acciones` |
| `fecha_eliminacion` por defecto 30-11; no anterior a `fecha_fin` | `test_configuracion::test_crear_proceso_en_borrador_con_sus_fases_de_sistema`, `test_configuracion::test_crear_proceso_valida_los_datos` |
| Proceso ACTIVO con la fecha vencida: no se elimina y se alerta | `test_retencion::test_no_se_repite_y_no_toca_procesos_activos`, `test_retencion_pantallas::test_comando_alerta_si_el_proceso_sigue_activo`, `test_retencion::test_avisos_del_panel` |
| Al vencer: borra expedientes y datos, anonimiza la auditoría, conserva totales | `test_retencion::test_elimina_los_datos_del_proceso_vencido`, `test_retencion::test_la_auditoria_se_anonimiza_y_no_se_borra` |
| Un postulante con expediente más reciente conserva sus datos | `test_retencion::test_elimina_los_datos_del_proceso_vencido` |
| Aviso 30 días antes en el panel del Administrador y del Gestor | `test_retencion::test_avisos_del_panel`, `test_retencion_pantallas::test_aviso_en_el_panel` |

## Sesión y auditoría

| Criterio | Prueba |
|---|---|
| Con `must_change_password` no se accede a nada hasta cambiarla | `test_auth::test_must_change_password_bloquea_todas_las_pantallas` |
| Cada acción genera exactamente un registro, que no se puede modificar | `test_criterios::test_cada_accion_genera_exactamente_un_registro`, `test_auditoria_consulta::test_todas_las_acciones_auditadas_tienen_nombre` (no hay ninguna ruta que edite o borre auditoría; la única excepción es la anonimización de RF-26) |

## Rendimiento (RNF-04), medido

| Requisito | Medición |
|---|---|
| Sondeo del seguimiento < 300 ms | 1,8 ms sin cambios (304) y 11,6 ms con cambios |
| Carga masiva de 20.000 filas < 60 s | Vista previa 1,3 s; importar 5,7 s; sobrescribir 19 s |
| El correo no bloquea más de 3 s | Inicio de proceso en 0,04 s con un SMTP que tarda 4 s |
| Reportes con 5.000 expedientes | 0,2 a 0,4 s cada uno |
