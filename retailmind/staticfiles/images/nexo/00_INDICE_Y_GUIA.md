# 📚 ÍNDICE DE DOCUMENTACIÓN - FACTURACIÓN ELECTRÓNICA CHILE

## Bienvenido al Sistema de Documentación para Emisión de DTE

He analizado las estructuras de datos de tu sistema de facturación Acepta y he preparado **4 documentos completos** para ayudarte a implementar la emisión de documentos tributarios electrónicos en Chile.

---

## 📄 DOCUMENTOS DISPONIBLES

### 1️⃣ **estructura_datos_dte_chile.md** (22 KB)
**📖 DOCUMENTO TÉCNICO COMPLETO**

**Contenido:**
- Estructura detallada de todos los tipos de DTE (Facturas, Boletas, Guías, Notas)
- Especificación completa de campos (nombre, tipo, largo, TAG XML)
- Códigos de impuestos y retenciones
- Validaciones requeridas por el SII
- Estructura de directorios del sistema Acepta
- Bitácora y respuestas de procesamiento

**Cuándo usarlo:**
- Implementación técnica del sistema
- Desarrollo de software de facturación
- Integración con APIs
- Documentación para desarrolladores

**Ideal para:** Desarrolladores, programadores, arquitectos de software

---

### 2️⃣ **guia_rapida_dte.md** (5.3 KB)
**⚡ REFERENCIA RÁPIDA**

**Contenido:**
- Datos básicos necesarios para cada documento
- Códigos más comunes (tipos de documento, indicadores, etc.)
- Plantillas de prompt listas para usar
- Checklist antes de emitir
- Errores comunes a evitar
- Tips y recomendaciones

**Cuándo usarlo:**
- Cuando necesites emitir un documento rápidamente
- Como referencia de escritorio
- Para capacitar usuarios
- Consultas rápidas del día a día

**Ideal para:** Usuarios finales, personal de ventas, administrativos

---

### 3️⃣ **ejemplos_txt_acepta.md** (8.7 KB)
**💾 EJEMPLOS PRÁCTICOS**

**Contenido:**
- Formato completo del archivo TXT
- Ejemplo detallado de cada tipo de documento:
  - Factura Electrónica (33)
  - Boleta Electrónica (39)
  - Guía de Despacho (52)
  - Nota de Crédito (61)
  - Factura Exenta (34)
- Explicación línea por línea
- Notas sobre formato y validación
- Herramientas recomendadas

**Cuándo usarlo:**
- Generar archivos TXT para el sistema Acepta
- Entender el formato de entrada
- Debugging de archivos rechazados
- Crear templates de archivos

**Ideal para:** Desarrolladores, integradores, personal técnico

---

### 4️⃣ **tabla_comparativa_dte.md** (10 KB)
**📊 COMPARACIÓN Y DECISIÓN**

**Contenido:**
- Tabla comparativa entre todos los tipos de DTE
- Campos obligatorios por tipo de documento
- Casos de uso típicos
- Flujos de trabajo comunes
- Matriz de decisión: "¿Qué documento debo emitir?"
- Diferencias clave entre documentos similares
- Validaciones específicas por tipo

**Cuándo usarlo:**
- Decidir qué tipo de documento emitir
- Entender diferencias entre documentos
- Planificar flujos de trabajo
- Capacitación de personal

**Ideal para:** Gerentes, supervisores, nuevos usuarios, capacitadores

---

## 🎯 GUÍA DE USO SEGÚN TU NECESIDAD

### Si necesitas...

**❓ Emitir un documento ahora mismo**
→ Lee: `guia_rapida_dte.md`
→ Usa las plantillas de prompt que vienen ahí

**💻 Desarrollar/programar integración**
→ Lee: `estructura_datos_dte_chile.md` (completo)
→ Luego: `ejemplos_txt_acepta.md` (para implementar)

**🤔 Entender qué documento usar**
→ Lee: `tabla_comparativa_dte.md`
→ Usa la matriz de decisión

**🔧 Generar archivos TXT**
→ Lee: `ejemplos_txt_acepta.md`
→ Copia y adapta los ejemplos

**📚 Capacitar personal**
→ Comienza con: `guia_rapida_dte.md`
→ Profundiza con: `tabla_comparativa_dte.md`

**🐛 Resolver errores**
→ Revisa: `estructura_datos_dte_chile.md` (validaciones)
→ Compara con: `ejemplos_txt_acepta.md` (formato correcto)

---

## 📋 RESUMEN DE TIPOS DE DOCUMENTOS

| Código | Nombre | Archivo Principal | Para qué se usa |
|--------|--------|-------------------|-----------------|
| **33** | Factura Electrónica | Todos los docs | Venta B2B con IVA |
| **34** | Factura Exenta | Todos los docs | Venta B2B sin IVA |
| **39** | Boleta Electrónica | Todos los docs | Venta a consumidor final con IVA |
| **41** | Boleta Exenta | Todos los docs | Venta a consumidor final sin IVA |
| **52** | Guía de Despacho | Todos los docs | Traslado de mercaderías |
| **56** | Nota de Débito | estructura + tabla | Aumentar valor de documento |
| **61** | Nota de Crédito | Todos los docs | Anular/disminuir documento |

---

## 🚀 INICIO RÁPIDO

### Para usuarios nuevos:

1. **Primero**: Lee la `guia_rapida_dte.md` completa (5 minutos)
2. **Segundo**: Revisa la `tabla_comparativa_dte.md` para entender las diferencias
3. **Tercero**: Usa las plantillas de prompt para emitir tu primer documento

### Para desarrolladores:

1. **Primero**: Lee la `estructura_datos_dte_chile.md` completa
2. **Segundo**: Estudia los `ejemplos_txt_acepta.md`
3. **Tercero**: Implementa con los ejemplos como base
4. **Cuarto**: Usa la `tabla_comparativa_dte.md` para validaciones

---

## 💡 INFORMACIÓN IMPORTANTE EXTRAÍDA DE TUS ARCHIVOS

### Sistema Acepta - Configuración Detectada

**Estructura de Directorios:**
```
/Acepta/DTEService/custodium.com/
├── dte-pruebas/      (ambiente de pruebas)
└── dte-produccion/   (ambiente productivo)
```

**Archivos Importantes:**
- **CAF** (Códigos Autorización Folios): `/etc/cert/caf/`
- **Certificados Digitales**: `/etc/cert/pki/`
- **Archivos Procesados OK**: `/var/ca4xml/output/done/`
- **Archivos con Error**: `/var/ca4xml/output/errors/`
- **PDFs Generados**: `/var/ca4xml/output/archivos-pdf/`

**Proceso de Emisión:**
1. Tu sistema genera archivo TXT con los datos
2. Se coloca en cola de procesamiento
3. Acepta valida, firma y timbra el documento
4. Genera PDF y XML
5. Sube al SII
6. Retorna URL del documento electrónico

---

## 📞 SOPORTE Y REFERENCIAS

### Documentos Analizados
- `2015-05-19_EstructDirectorios.xls` - Estructura de directorios Acepta
- `04-07-2016_MSG_ENT_TXT_DTE_NAC.xlsx` - Formato entrada DTE Nacional
- `07-06-2016_MSG_ENTRADA_TXT_BOLETAS.xlsx` - Formato entrada Boletas

### Referencias Oficiales
- **SII Chile**: https://www.sii.cl
- **Portal SII**: https://maullin.sii.cl (certificación)
- **Documentación Técnica SII**: Buscar "Formato DTE SII"

---

## ⚠️ NOTAS IMPORTANTES

1. **Fecha de los Documentos Base**: Los archivos analizados son de 2016, algunas especificaciones del SII pueden haber cambiado. Verifica con la documentación actual del SII.

2. **Tasa de IVA**: Actualmente es 19%. Si cambia, actualizar en todos los documentos.

3. **Certificados Digitales**: Deben estar vigentes y correctamente instalados.

4. **Folios**: Solicitar al SII con anticipación suficiente.

5. **Ambiente de Pruebas**: Siempre probar primero en ambiente de certificación.

---

## 🔄 ACTUALIZACIONES Y MANTENIMIENTO

**Estos documentos deben actualizarse cuando:**
- Cambie la tasa de IVA
- El SII modifique estructuras de datos
- Se agreguen nuevos tipos de documentos
- Cambien las validaciones del SII
- Se actualice el sistema Acepta

---

## ✅ CHECKLIST DE IMPLEMENTACIÓN

### Antes de emitir en producción:

- [ ] Certificado digital instalado y vigente
- [ ] CAFs (folios) solicitados y cargados
- [ ] Sistema configurado en ambiente de certificación
- [ ] Pruebas realizadas con todos los tipos de documentos
- [ ] Personal capacitado con estos documentos
- [ ] Validación SII OK en certificación
- [ ] Respaldo de archivos configurado
- [ ] Monitoreo de errores implementado

---

## 📧 SIGUIENTES PASOS

### Si tienes dudas sobre:

**Estructura de datos específicos**
→ Consulta `estructura_datos_dte_chile.md`

**Cómo emitir un documento**
→ Usa las plantillas en `guia_rapida_dte.md`

**Formato del archivo TXT**
→ Revisa `ejemplos_txt_acepta.md`

**Qué tipo de documento usar**
→ Usa la matriz de decisión en `tabla_comparativa_dte.md`

---

## 🎓 RECURSOS ADICIONALES RECOMENDADOS

1. **Portal SII** - Documentación oficial
2. **Foro Acepta** - Consultas específicas del sistema
3. **Capacitación SII** - Cursos sobre facturación electrónica
4. **Asociación de Contribuyentes** - Orientación tributaria

---

## ✨ RESUMEN FINAL

Has recibido **4 documentos completos** con:
- ✅ Estructura técnica detallada de todos los DTE
- ✅ Guía rápida de referencia diaria
- ✅ Ejemplos prácticos de archivos TXT
- ✅ Tablas comparativas y flujos de trabajo

**Total de páginas**: ~45 páginas de documentación práctica

**Tiempo estimado de lectura completa**: 60-90 minutos

**Valor agregado**: 
- Ahorro de horas de investigación
- Información consolidada y organizada
- Ejemplos listos para usar
- Referencias técnicas precisas

---

**¡Éxito con tu implementación de facturación electrónica!** 🚀

---

*Documentación generada por análisis de archivos del sistema Acepta*  
*Fecha: Noviembre 2025*  
*Basado en archivos MSG 2016 y estructura Acepta 2015*
