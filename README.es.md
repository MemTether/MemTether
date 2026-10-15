<div align="center">

<img src="assets/demo.gif" width="100%" alt="MemTether — memoria AI multiplataforma en acción"/>

# MemTether — El Tribunal de Memoria AI

**Tu agente recuerda cosas. ¿Quién audita lo que recuerda?**

MemTether es un centro de memoria local-first (local-primero) para agentes AI multi-cliente. Mientras la mayoría de sistemas de memoria compiten por la precisión de recuperación, MemTether compite por la **gobernanza**: quién escribió cada hecho, cuándo entró en vigor, qué lo reemplazó, qué conflictos fueron resueltos por un humano, y una **cadena de hash a prueba de manipulaciones** que cualquiera puede verificar — sin código, sin dependencias. La recuperación está disponible (enrutamiento SQL determinista SGM + embedding híbrido), pero no es el titular: una memoria de agente que puedes **auditar, corregir y confiar** es.

¿Vienes de mem0, Zep o Letta? [`examples/mem0_migration.py`](examples/mem0_migration.py)
muestra una migración completa de exportación JSON de mem0 — no se requiere SDK de mem0.

```bash
$ memtether court fact-20261007-abc
+============================================================+
| MEMTETHER MEMORY COURT - EXPEDIENTE DEL CASO                |
| 1. CONTENIDO  El puerto del proxy Clash es 7897             |
| 2. TEMPORAL   válido: 10-05 → presente | conocido: 10-05 → ahora |
| 3. PROCEDENCIA #1 recordar por=codex → #2 corregir por=user |
| 4. CONFLICTOS caso #3: vs fact-...old veredicto=reemplazado |
| 5. INTEGRIDAD cadena: PASS (3 anclas, 42 entradas hasheadas)|
+============================================================+
```

## MemTether en 30 segundos

- **¿Tus clientes AI no se conocen entre sí?** Cada agente mantiene su propia memoria, sin saber lo que los demás saben. MemTether usa punteros a nivel de archivo para que compartan el mismo cerebro gobernado — cero nube, cero proceso de sincronización.
- **¿Tu AI escribió algo que no debería?** Cada memoria incluye identidad de procedencia y auditoría de cadena hash. Si alguien modifica la base de datos directamente, la cadena se rompe inmediatamente — ejecuta `python examples/audit_chain_demo.py` para verlo.
- **¿Tu AI cambió de opinión?** Las correcciones nunca se borran. La cadena bi-temporal de reemplazo registra quién cambió qué, cuándo y por qué, con capacidad de consulta en cualquier punto histórico.

Pruébalo tú mismo:

```text
$ python examples/audit_chain_demo.py
Escribiendo 25 memorias (generando 2 anclas de auditoría automáticamente)...
Antes de alterar: verify_chain: PASS {'anchors': 2, 'entries_covered': 20}
Simulando atacante alterando la base de datos directamente...
Después de alterar: verify_chain: FAIL {'reason': 'chain hash mismatch'}
✅ ¡La cadena hash detectó la manipulación!

> Nota: entries_covered: 20 de 25 escritas es normal — la cadena hash genera
> un ancla cada 10 registros de auditoría, y las últimas 5 están en cola no anclada
> hasta el siguiente lote. Solo las entradas "ancladas" violadas activan la alerta roja.
```

## Migración desde mem0

¿Ya usas mem0? [`examples/mem0_migration.py`](examples/mem0_migration.py)
demuestra el ciclo completo: exportación JSON de mem0 → MemTether Exchange v1 → búsqueda en línea → reescritura en formato mem0.
Sin SDK de mem0, los campos bi-temporales marcan honestamente `temporal_source="backfilled"`.

## Características principales

### Gobernanza de memoria completa

MemTether registra cinco dimensiones por cada hecho:
1. **Contenido** — el hecho en sí
2. **Temporal** — `valid_from/to` (tiempo de negocio) + `recorded_at/invalidated_at` (tiempo de sistema)
3. **Procedencia** — quién escribió cada versión
4. **Conflictos** — historial de resolución de conflictos
5. **Integridad** — cadena de hash a prueba de manipulaciones

### Correcciones que nunca se borran

```bash
# Lunes: Claude Code escribe
$ memtether remember "Desplegar en puerto 8080" --source claude-code

# Miércoles: lo corriges desde Cursor
$ memtether correct fact-xxx "Desplegar en puerto 9090" --reason "puerto cambiado"

# Viernes: otro agente busca
$ memtether search "puerto desplegar" --list
  1. [hecho] Desplegar en puerto 9090 (activo)          ← verdad actual
     "puerto 8080" fue reemplazado, no eliminado.
     Cadena completa: memtether timeline fact-xxx
```

### Defensa contra envenenamiento (OWASP LLM01/02/06)

```bash
$ memtether audit --level=owasp
✓ LLM01: Hechos con procedencia verificada
✓ LLM02: Cadena de hash a prueba de manipulaciones
✓ LLM06: Retención de registros de corrección
```

### 23 adaptadores multiplataforma

[`adapters/`](adapters/) — Claude Code, Cursor, Windsurf, Codex, Gemini CLI, Jan, Continue, Roo, Cody, FF-CLI, Aider, and more.

## Quick Start

```bash
pip install memtether -i https://pypi.org/simple/
memtether init    # crea DB + conecta todos los clientes AI detectados
```

Eso es todo. MemTether detecta Claude Code, Cursor, Windsurf, Codex, Gemini CLI y más clientes en tu máquina, escribe la configuración MCP y verifica.

<details>
<summary>Más opciones de instalación</summary>

```bash
# Un cliente a la vez
memtether setup claude-code
memtether setup cursor
memtether setup gemini-cli    # ejecuta `memtether setup list` para ver los 23

# Con búsqueda semántica (embedding local, sin nube):
pip install "memtether[vector]"          # 1. dependencias (chromadb, onnxruntime)
memtether download-models                # 2. pesos (bge-m3-int8 ~560MB, o --profile bge-small-zh ~46MB)
python -m memsearch --rebuild            # 3. construir el índice vectorial
# Omite cualquiera de estos y la búsqueda degrada gracefulmente a palabra clave + BM25.

# Con servidor API REST + panel web
pip install "memtether[server]"
memtether dashboard

# Docker
docker-compose up
```

</details>

## Comparación con alternativas

| | MemTether | engram | mem0 | agent-memory |
|---|---|---|---|---|
| Almacenamiento | SQLite vía punteros de archivo | SQLite (binario Go) | API en la nube | Archivos Markdown |
| Reemplazo | ✅ bi-temporal, nunca elimina | ❌ | ⚠️ parcial | ✅ |
| Atribución de procedencia | ✅ cada hecho etiquetado | ❌ | ❌ | ❌ |
| Retroalimentación Q-Value | ✅ memorias usadas rankean más alto | ❌ | ❌ | ❌ |
| Defensa contra envenenamiento | ✅ OWASP LLM01/02/06 | ❌ | ⚠️ verificación de fidelidad | ❌ |
| Multi-cliente | ✅ punteros a nivel de archivo (23 adaptadores) | ✅ MCP | ✅ API | ✅ CLI |
| Nube requerida | ❌ | opcional | ✅ | ❌ |

---

[**Página del proyecto**](https://huggingface.co/spaces/lanbass/memtether-demo) · [Instalar](#quick-start) · [Docs](https://memtether.github.io/MemTether/site/en/) · [Reproducir](https://memtether.github.io/MemTether/site/en/reproduce.html) · [English](README.md) · [中文文档](README.zh-CN.md)

[![PyPI](https://img.shields.io/pypi/v/memtether)](https://pypi.org/project/memtether/)
[![CI](https://github.com/MemTether/MemTether/actions/workflows/ci.yml/badge.svg)](https://github.com/MemTether/MemTether/actions)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

mcp-name: io.github.lanbass869-cell/memtether
[![MCP Registry](https://img.shields.io/badge/MCP_Registry-io.github.lanbass869--cell%2Fmemtether-blue)](https://registry.modelcontextprotocol.io)
