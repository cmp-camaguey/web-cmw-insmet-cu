/**
 * RUM beacon — lo que sólo el navegador puede medir.
 *
 * Envía tres tipos de evento al endpoint `/rum/collect/`:
 *   pageview  al cargar: ruta, referrer, peso transferido y recursos lentos.
 *   vitals    al salir:  LCP, FCP, INP (aproximado) y CLS.
 *   error     al ocurrir: excepciones no controladas (máximo 5 por página).
 *
 * Reglas de la casa:
 * - JS vanilla, sin dependencias ni build.
 * - `navigator.sendBeacon`: si el visitante cierra la pestaña, el último
 *   evento igual sale. `fetch(keepalive)` es el respaldo.
 * - Un fallo de red nunca se muestra ni se reintenta en bucle: es telemetría.
 * - INP se aproxima con la interacción más lenta observada en la página; el
 *   INP oficial es un percentil de la sesión completa, que un sitio
 *   multipágina no puede calcular sin otro almacenamiento.
 */
;(function () {
  'use strict'

  var config = document.getElementById('rumConfig')
  if (!config) return
  var endpoint = config.getAttribute('data-endpoint')
  if (!endpoint) return

  var STORAGE_KEY = 'rum_session'
  var SESSION_TTL_MS = 30 * 60 * 1000
  var SLOW_RESOURCE_MS = 500
  var MAX_ERRORS = 5
  var MAX_MESSAGE = 300

  var errorsSent = 0
  var vitalsSent = false
  var lcp = null
  var cls = 0
  var inp = null
  var fcp = null

  function now() {
    return Date.now()
  }

  function sessionId() {
    try {
      var stored = window.localStorage.getItem(STORAGE_KEY)
      if (stored) {
        var parsed = JSON.parse(stored)
        if (parsed && parsed.id && parsed.expires > now()) {
          window.localStorage.setItem(
            STORAGE_KEY,
            JSON.stringify({ id: parsed.id, expires: now() + SESSION_TTL_MS })
          )
          return parsed.id
        }
      }
      var id = newId()
      window.localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ id: id, expires: now() + SESSION_TTL_MS })
      )
      return id
    } catch (error) {
      return newId()
    }
  }

  function newId() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
      return window.crypto.randomUUID()
    }
    return 'xxxxxxxxyxxxx'.replace(/[xy]/g, function (character) {
      var random = (Math.random() * 16) | 0
      var value = character === 'x' ? random : (random & 0x3) | 0x8
      return value.toString(16)
    })
  }

  var sid = sessionId()

  function send(payload) {
    payload.sid = sid
    var body = JSON.stringify(payload)
    try {
      if (navigator.sendBeacon && body.length < 8192) {
        var blob = new Blob([body], { type: 'application/json' })
        if (navigator.sendBeacon(endpoint, blob)) return
      }
    } catch (error) {
      /* cae al fetch de abajo */
    }
    try {
      fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body,
        keepalive: true,
        credentials: 'same-origin'
      }).catch(function () {})
    } catch (error) {
      /* telemetría: no hay nada que hacer */
    }
  }

  function pagePath() {
    return window.location.pathname.slice(0, 255)
  }

  function resourceWeight() {
    if (!window.performance || typeof window.performance.getEntriesByType !== 'function') {
      return { weight: null, slow: 0 }
    }
    var entries = window.performance.getEntriesByType('resource')
    var bytes = 0
    var slow = 0
    for (var index = 0; index < entries.length; index += 1) {
      var entry = entries[index]
      bytes += entry.transferSize || 0
      if (entry.duration > SLOW_RESOURCE_MS) slow += 1
    }
    if (!bytes) return { weight: null, slow: slow }
    return { weight: Math.round(bytes / 1024), slow: slow }
  }

  function sendPageview() {
    var weight = resourceWeight()
    send({
      kind: 'pageview',
      path: pagePath(),
      referrer: document.referrer || '',
      weight_kb: weight.weight,
      slow_resources: weight.slow
    })
  }

  function sendVitals() {
    if (vitalsSent) return
    var metrics = {}
    if (lcp !== null && lcp > 0) metrics.lcp = Math.round(lcp)
    if (fcp !== null && fcp > 0) metrics.fcp = Math.round(fcp)
    if (inp !== null && inp > 0) metrics.inp = Math.round(inp)
    if (cls >= 0) metrics.cls = Math.round(cls * 1000) / 1000
    if (!metrics.lcp && !metrics.fcp && !metrics.inp && !metrics.cls) return
    vitalsSent = true
    send({ kind: 'vitals', path: pagePath(), metrics: metrics })
  }

  function observe(type, callback, options) {
    try {
      if (!window.PerformanceObserver) return
      var observer = new window.PerformanceObserver(function (list) {
        list.getEntries().forEach(callback)
      })
      observer.observe(Object.assign({ type: type, buffered: true }, options || {}))
    } catch (error) {
      /* el navegador no soporta ese tipo de entrada */
    }
  }

  observe('largest-contentful-paint', function (entry) {
    lcp = entry.startTime + (entry.duration || 0)
  })

  observe('layout-shift', function (entry) {
    if (!entry.hadRecentInput) cls += entry.value
  })

  observe('paint', function (entry) {
    if (entry.name === 'first-contentful-paint') fcp = entry.startTime
  })

  observe(
    'event',
    function (entry) {
      if (entry.interactionId && entry.duration > (inp || 0)) inp = entry.duration
    },
    { durationThreshold: 40 }
  )

  observe('navigation', function (entry) {
    if (fcp === null && entry.responseStart) fcp = entry.responseStart
  })

  function reportError(event) {
    if (errorsSent >= MAX_ERRORS) return
    var message = ''
    var source = ''
    var lineno = null
    if (event && event.message) {
      message = event.message
      source = event.filename || ''
      lineno = event.lineno || null
    } else if (event && event.reason) {
      message = event.reason.message || String(event.reason)
      source = event.reason.filename || ''
      lineno = event.reason.lineno || null
    }
    if (!message) return
    errorsSent += 1
    send({
      kind: 'error',
      path: pagePath(),
      message: message.slice(0, MAX_MESSAGE),
      source: source.slice(0, 255),
      lineno: lineno
    })
  }

  window.addEventListener('error', reportError)
  window.addEventListener('unhandledrejection', reportError)

  window.addEventListener('pagehide', function (event) {
    if (event.persisted) return
    sendVitals()
  })

  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') sendVitals()
  })

  // El pageview espera a `load` para poder pesar la página con todos sus
  // recursos; si `load` ya pasó (script inyectado tarde), sale enseguida.
  // Los vitals se cierran a los 20 s o al ocultarse la pestaña, lo que ocurra
  // primero: una muestra por carga, sin duplicados.
  if (document.readyState === 'complete') {
    sendPageview()
    window.setTimeout(sendVitals, 20000)
  } else {
    window.addEventListener('load', function () {
      sendPageview()
      window.setTimeout(sendVitals, 20000)
    })
  }
})()
