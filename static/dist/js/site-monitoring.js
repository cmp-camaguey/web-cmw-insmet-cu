/**
 * Monitoreo del sitio — auto-refresh del panel.
 *
 * El servidor pinta la primera foto completa. Este archivo sólo vuelve a pedir
 * el mismo snapshot cada pocos segundos y reemplaza los valores del DOM, sin
 * recargar la página ni reconstruir las tarjetas.
 *
 * Reglas de la casa:
 * - JS vanilla, sin dependencias ni build.
 * - Los números se formatean igual que en la plantilla (misma cantidad de
 *   decimales), para que el valor no "salte" al llegar el primer refresco.
 * - Todo texto que viene del servidor se escribe con textContent: una ruta con
 *   caracteres raros no puede inyectar HTML.
 */
;(function () {
  'use strict'

  var root = document.getElementById('monitoringRoot')
  if (!root) return

  var endpoint = root.getAttribute('data-url')
  var interval = parseInt(root.getAttribute('data-refresh-ms'), 10) || 5000
  var toggleButton = document.getElementById('monitoringToggle')
  var refreshButton = document.getElementById('monitoringRefresh')

  var EMPTY = '\u2014'
  var COLORS = ['success', 'warning', 'danger', 'primary', 'info', 'secondary']
  var STATE_COLORS = {
    ok: 'success',
    good: 'success',
    degraded: 'warning',
    'needs-improvement': 'warning',
    down: 'danger',
    poor: 'danger',
    nodata: 'secondary'
  }

  var timer = null
  var paused = false
  var inFlight = false

  function get(obj, path) {
    return path.split('.').reduce(function (acc, key) {
      if (acc === null || acc === undefined) return undefined
      return acc[key]
    }, obj)
  }

  function toNumber(value) {
    if (value === null || value === undefined || value === '') return null
    var number = Number(value)
    return isNaN(number) ? null : number
  }

  function clampPercent(value) {
    var number = toNumber(value)
    if (number === null) return null
    return Math.max(0, Math.min(100, number))
  }

  function trimZeros(text) {
    return text.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1')
  }

  var formatters = {
    text: function (value) {
      return value === null || value === undefined || value === '' ? EMPTY : String(value)
    },
    int: function (value) {
      var number = toNumber(value)
      return number === null ? EMPTY : number.toLocaleString('es-MX', { maximumFractionDigits: 0 })
    },
    num1: function (value) {
      var number = toNumber(value)
      return number === null
        ? EMPTY
        : number.toLocaleString('es-MX', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
    },
    num2: function (value) {
      var number = toNumber(value)
      return number === null
        ? EMPTY
        : number.toLocaleString('es-MX', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    },
    pct: function (value) {
      var number = toNumber(value)
      return number === null ? EMPTY : trimZeros(number.toFixed(2)) + '%'
    },
    ms: function (value) {
      var number = toNumber(value)
      return number === null ? EMPTY : Math.round(number) + ' ms'
    }
  }

  function format(value, kind) {
    var formatter = formatters[kind] || formatters.text
    return formatter(value)
  }

  function replaceColorClasses(element, prefix, color) {
    COLORS.forEach(function (name) {
      element.classList.remove(prefix + name)
      element.classList.remove(prefix + name + '-lt')
    })
    element.classList.add(prefix + color)
  }

  function setBadge(element, color) {
    COLORS.forEach(function (name) {
      element.classList.remove('bg-' + name)
      element.classList.remove('bg-' + name + '-lt')
    })
    element.classList.add('bg-' + color + '-lt')
  }

  function setBarColor(element, color) {
    COLORS.forEach(function (name) {
      element.classList.remove('bg-' + name)
    })
    element.classList.add('bg-' + color)
  }

  function barColor(kind, value) {
    var number = toNumber(value)
    if (number === null) return 'secondary'
    if (kind === 'swap') return number >= 50 ? 'warning' : 'success'
    if (number >= 90) return 'danger'
    if (number >= 70) return 'warning'
    return 'success'
  }

  function coreColor(value) {
    var number = toNumber(value) || 0
    if (number >= 90) return 'danger'
    if (number >= 70) return 'warning'
    return 'primary'
  }

  function escapeAttribute(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/"/g, '&quot;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
  }

  function applyValues(snapshot) {
    document.querySelectorAll('[data-live]').forEach(function (element) {
      var value = get(snapshot, element.getAttribute('data-live'))
      if (value === undefined) return
      element.textContent = format(value, element.getAttribute('data-fmt'))
    })

    document.querySelectorAll('[data-live-width]').forEach(function (element) {
      var percent = clampPercent(get(snapshot, element.getAttribute('data-live-width')))
      if (percent === null) {
        element.style.width = '0%'
        return
      }
      element.style.width = percent + '%'
      var kind = element.getAttribute('data-live-class')
      if (kind) setBarColor(element, barColor(kind, percent))
    })
  }

  function applyStatus(snapshot) {
    var status = snapshot.status || {}
    var color = STATE_COLORS[status.level] || 'secondary'

    var label = document.querySelector('[data-status-label]')
    if (label) label.textContent = status.label || EMPTY

    var badge = document.querySelector('[data-status-badge]')
    if (badge) {
      setBadge(badge, color)
      badge.textContent = status.level || EMPTY
    }

    var dot = document.querySelector('[data-status-dot]')
    if (dot) replaceColorClasses(dot, 'status-', color)

    var reasons = status.reasons || []
    var alert = document.querySelector('[data-status-alert]')
    if (alert) {
      var alertTitle = alert.querySelector('[data-status-alert-title]')
      var list = alert.querySelector('[data-status-reasons]')
      if (alertTitle) alertTitle.textContent = 'Estado ' + (status.label || EMPTY)
      if (list) {
        list.innerHTML = ''
        reasons.forEach(function (reason) {
          var item = document.createElement('li')
          item.textContent = reason
          list.appendChild(item)
        })
      }
      alert.classList.remove('alert-danger', 'alert-warning')
      alert.classList.add(status.level === 'down' ? 'alert-danger' : 'alert-warning')
      alert.classList.toggle('d-none', reasons.length === 0)
    }

    var incident = document.querySelector('[data-incident-line]')
    if (incident) {
      if (snapshot.incident && snapshot.incident.active) {
        incident.innerHTML =
          '<span class="text-danger"><i class="icon ti ti-alert-triangle"></i> ' +
          'Incidente activo: ' +
          escapeAttribute(snapshot.incident.duration_display || EMPTY) +
          '</span>'
      } else {
        incident.textContent = 'Sin incidentes activos'
      }
    }
  }

  function applyDependencies(snapshot) {
    document.querySelectorAll('[data-state-badge]').forEach(function (element) {
      var path = element.getAttribute('data-state-badge')
      var ok = get(snapshot, path) === true
      var color = ok ? 'success' : path === 'health.queue.ok' ? 'warning' : 'danger'
      setBadge(element, color)
      element.textContent =
        path === 'health.queue.ok'
          ? ok
            ? 'Al día'
            : 'Atrasado'
          : ok
            ? 'Responde'
            : 'No responde'
    })
  }

  function applyUptimeSlots(snapshot) {
    var container = document.querySelector('[data-uptime-slots]')
    var slots = snapshot.uptime && snapshot.uptime.slots
    if (!container || !Array.isArray(slots)) return
    var html = ''
    slots.forEach(function (slot) {
      var title =
        slot.label + ' \u00b7 ' + slot.total + ' peticiones \u00b7 ' + slot.errors + ' errores'
      html +=
        '<div class="tracking-block bg-' +
        (STATE_COLORS[slot.state] || 'secondary') +
        '" title="' +
        escapeAttribute(title) +
        '"></div>'
    })
    container.innerHTML = html
  }

  function applyCores(snapshot) {
    var container = document.querySelector('[data-cores]')
    var cpu = snapshot.system && snapshot.system.cpu
    if (!container || !cpu || !Array.isArray(cpu.cores)) return
    var html = ''
    cpu.cores.forEach(function (core, index) {
      html +=
        '<span class="monitoring-core" title="N\u00facleo ' +
        (index + 1) +
        ': ' +
        core +
        '%"><span class="monitoring-core-bar bg-' +
        coreColor(core) +
        '" style="height: ' +
        Math.max(0, Math.min(100, Number(core) || 0)) +
        '%"></span></span>'
    })
    container.innerHTML = html
  }

  function renderSparkline(name, series, key) {
    var svg = document.querySelector('[data-spark="' + name + '"]')
    if (!svg || !Array.isArray(series) || series.length === 0) return
    var values = series.map(function (point) {
      return Number(point[key]) || 0
    })
    var max = Math.max.apply(null, values.concat([1]))
    var width = 100
    var height = 40
    var step = values.length > 1 ? width / (values.length - 1) : width
    var points = values.map(function (value, index) {
      var x = (index * step).toFixed(2)
      var y = (height - (value / max) * height).toFixed(2)
      return x + ',' + y
    })
    var line = points.join(' ')
    var area = '0,' + height + ' ' + line + ' ' + width + ',' + height
    svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height)
    svg.setAttribute('preserveAspectRatio', 'none')
    svg.innerHTML =
      '<polygon points="' +
      area +
      '" fill="rgba(6,111,209,0.12)"></polygon>' +
      '<polyline points="' +
      line +
      '" fill="none" stroke="var(--tblr-primary)" stroke-width="1.5" ' +
      'vector-effect="non-scaling-stroke"></polyline>'
  }

  function renderRows(tbody, rows) {
    var table = tbody.closest('table')
    var columns = table ? table.querySelectorAll('thead th').length : rows[0].length
    tbody.innerHTML = ''
    if (rows.length === 0) {
      var emptyRow = document.createElement('tr')
      var emptyCell = document.createElement('td')
      emptyCell.colSpan = columns || 1
      emptyCell.className = 'text-secondary'
      emptyCell.textContent = 'Sin datos todav\u00eda.'
      emptyRow.appendChild(emptyCell)
      tbody.appendChild(emptyRow)
      return
    }
    rows.forEach(function (cells) {
      var row = document.createElement('tr')
      cells.forEach(function (value, index) {
        var cell = document.createElement('td')
        if (index > 0) cell.className = 'text-end'
        cell.textContent = value === null || value === undefined ? EMPTY : String(value)
        row.appendChild(cell)
      })
      tbody.appendChild(row)
    })
  }

  function applyTables(snapshot) {
    var traffic = snapshot.traffic || {}
    var tables = {
      top_pages: (traffic.top_pages || []).map(function (row) {
        return [row.path, row.views]
      }),
      referrers: ((traffic.referrers || {}).hosts || []).map(function (row) {
        return [row.label, row.count]
      }),
      devices: (traffic.devices || []).map(function (row) {
        return [row.label, row.count]
      }),
      browsers: (traffic.browsers || []).map(function (row) {
        return [row.browser, row.count]
      }),
      errors: ((snapshot.errors || {}).items || []).map(function (row) {
        return [row.at, row.message, row.path]
      })
    }
    Object.keys(tables).forEach(function (name) {
      var tbody = document.querySelector('[data-table="' + name + '"]')
      if (tbody) renderRows(tbody, tables[name])
    })

    var empty = document.querySelector('[data-traffic-empty]')
    if (empty) empty.classList.toggle('d-none', Boolean(traffic.has_data))
  }

  function applyVitals(snapshot) {
    var metrics = (snapshot.vitals || {}).metrics
    if (!metrics) return
    Object.keys(metrics).forEach(function (key) {
      var block = document.querySelector('[data-vital-metric="' + key + '"]')
      var metric = metrics[key]
      if (!block || !metric) return

      var badge = block.querySelector('[data-vital-badge]')
      if (badge) {
        setBadge(badge, STATE_COLORS[metric.rating] || 'secondary')
        badge.textContent = metric.rating || 'sin datos'
      }

      var distribution = metric.distribution || {}
      var samples = metric.samples || 0
      ;['good', 'needs_improvement', 'poor'].forEach(function (rating) {
        var bar = block.querySelector('[data-vital-bar="' + rating + '"]')
        if (!bar) return
        var share = samples ? (100 * (distribution[rating] || 0)) / samples : 0
        bar.style.width = share + '%'
      })
    })
  }

  function applyConnection(ok) {
    var element = document.querySelector('[data-connection-error]')
    if (element) element.classList.toggle('d-none', ok)
  }

  function apply(snapshot) {
    applyValues(snapshot)
    applyStatus(snapshot)
    applyDependencies(snapshot)
    applyUptimeSlots(snapshot)
    applyCores(snapshot)
    applyVitals(snapshot)
    applyTables(snapshot)
    if (snapshot.http && snapshot.http.series) {
      renderSparkline('http', snapshot.http.series, 'count')
    }
    if (snapshot.traffic && snapshot.traffic.series) {
      renderSparkline('traffic', snapshot.traffic.series, 'count')
    }
  }

  function refresh() {
    if (inFlight) return Promise.resolve()
    inFlight = true
    return fetch(endpoint, {
      headers: { Accept: 'application/json' },
      credentials: 'same-origin'
    })
      .then(function (response) {
        if (!response.ok) throw new Error('HTTP ' + response.status)
        return response.json()
      })
      .then(function (snapshot) {
        apply(snapshot)
        applyConnection(true)
      })
      .catch(function () {
        // El panel no se vacía por un refresco fallido: se marca la pérdida de
        // conexión y se conserva la última foto buena.
        applyConnection(false)
      })
      .then(function () {
        inFlight = false
      })
  }

  function stop() {
    if (timer !== null) {
      window.clearInterval(timer)
      timer = null
    }
  }

  function start() {
    stop()
    if (paused || document.hidden) return
    timer = window.setInterval(refresh, interval)
  }

  function setPaused(next) {
    paused = next
    if (!toggleButton) return
    toggleButton.querySelector('i').className = paused
      ? 'icon ti ti-player-play me-1'
      : 'icon ti ti-pause me-1'
    var label = toggleButton.querySelector('[data-toggle-label]')
    if (label) label.textContent = paused ? 'Reanudar' : 'Pausar'
    if (paused) {
      stop()
    } else {
      refresh()
      start()
    }
  }

  if (toggleButton) {
    toggleButton.addEventListener('click', function () {
      setPaused(!paused)
    })
  }

  if (refreshButton) {
    refreshButton.addEventListener('click', function () {
      refresh()
    })
  }

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) {
      stop()
    } else if (!paused) {
      refresh()
      start()
    }
  })

  start()
})()
