;(function () {
  'use strict'

  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches

  // ── Telemetry Event Feed ─────────────────────────────

  var eventTypes = [
    { type: 'register', dot: 'register', msg: 'registered · firmware v{ver}' },
    { type: 'heartbeat', dot: 'heartbeat', msg: 'heartbeat · uptime {up}% · signal {sig}dBm' },
    { type: 'ota', dot: 'ota', msg: 'OTA · downloading v{ver} ({pct}%)' },
    { type: 'ota', dot: 'ota', msg: 'OTA · applying v{ver}' },
    { type: 'ota', dot: 'ota', msg: 'OTA · verifying v{ver}' },
    { type: 'heartbeat', dot: 'heartbeat', msg: 'heartbeat · uptime {up}% · signal {sig}dBm' },
    { type: 'config', dot: 'config', msg: 'config pushed · interval={n}s' },
    { type: 'ota', dot: 'ota', msg: 'OTA · hash_mismatch → rolling back' },
    { type: 'alert', dot: 'alert', msg: 'ALERT · device_offline · no heartbeat 300s' },
    { type: 'heartbeat', dot: 'heartbeat', msg: 'heartbeat · uptime {up}% · signal {sig}dBm' },
    { type: 'ota', dot: 'ota', msg: 'OTA · success v{ver}' },
    { type: 'register', dot: 'register', msg: 'registered · firmware v{ver}' }
  ]

  var deviceNames = [
    'sensor-a42', 'ev-ch-03', 'gw-07', 'sensor-b17', 'ev-ch-11',
    'sensor-c09', 'gw-04', 'ev-ch-08', 'sensor-a15', 'gw-12',
    'ev-ch-02', 'sensor-d01'
  ]

  var firmwareVersions = ['2.0.0', '1.8.3', '1.9.1', '2.1.0-beta', '1.7.2']

  function randomItem (arr) { return arr[Math.floor(Math.random() * arr.length)] }
  function randomInt (min, max) { return Math.floor(Math.random() * (max - min + 1)) + min }

  function generateEvent () {
    var tpl = randomItem(eventTypes)
    var device = randomItem(deviceNames)
    var ver = randomItem(firmwareVersions)
    var now = new Date()
    var time = now.toLocaleTimeString('en-US', { hour12: false })
    var msg = tpl.msg
      .replace(/\{ver\}/g, ver)
      .replace(/\{up\}/g, randomInt(97, 100))
      .replace(/\{sig\}/g, '-' + randomInt(35, 75))
      .replace(/\{pct\}/g, randomInt(10, 95))
      .replace(/\{n\}/g, randomInt(5, 60))

    return { time: time, device: device, msg: msg, dotClass: tpl.dot }
  }

  var eventList = document.getElementById('event-list')
  var maxEvents = 6
  var events = []

  function addEvent () {
    var evt = generateEvent()
    events.push(evt)
    if (events.length > maxEvents) events.shift()

    if (!eventList) return

    var item = document.createElement('div')
    item.className = 'event-item'
    if (!prefersReducedMotion) {
      item.style.animation = 'none'
      item.offsetHeight // trigger reflow
      item.style.animation = ''
    }
    item.innerHTML =
      '<span class="event-time">' + evt.time + '</span>' +
      '<span class="event-dot ' + evt.dotClass + '"></span>' +
      '<span class="event-device">' + evt.device + '</span>' +
      '<span class="event-msg">' + evt.msg + '</span>'

    eventList.appendChild(item)

    while (eventList.children.length > maxEvents) {
      eventList.removeChild(eventList.firstChild)
    }
  }

  // Seed initial events
  for (var i = 0; i < maxEvents; i++) { addEvent() }

  // Add new event every 3-5 seconds
  if (!prefersReducedMotion) {
    setInterval(addEvent, randomInt(2500, 4500))
  }

  // ── Animate stat counters ────────────────────────────
  function animateCounter (el, target) {
    if (prefersReducedMotion) {
      el.textContent = target
      return
    }
    var current = 0
    var step = Math.max(1, Math.floor(target / 30))
    var interval = setInterval(function () {
      current += step
      if (current >= target) {
        current = target
        clearInterval(interval)
      }
      el.textContent = current + (el.id === 'stat-rate' ? '%' : '')
    }, 40)
  }

  animateCounter(document.getElementById('stat-online'), 12)
  animateCounter(document.getElementById('stat-offline'), 2)
  animateCounter(document.getElementById('stat-ota'), 3)
  animateCounter(document.getElementById('stat-rate'), 94)

  // ── Mobile sidebar toggle ──────────────────────────
  var toggleBtn = document.getElementById('sidebarToggle')
  var sidebar = document.getElementById('sidebar')

  if (toggleBtn && sidebar) {
    toggleBtn.addEventListener('click', function () {
      sidebar.classList.toggle('open')
    })

    document.addEventListener('click', function (e) {
      if (
        window.innerWidth <= 768 &&
        sidebar.classList.contains('open') &&
        !sidebar.contains(e.target) &&
        e.target !== toggleBtn
      ) {
        sidebar.classList.remove('open')
      }
    })
  }

  // ── Active nav link tracking ───────────────────────
  var sections = document.querySelectorAll('.section')
  var navLinks = document.querySelectorAll('.sidebar-nav a')

  function updateActiveLink () {
    var scrollPos = window.scrollY + 120
    var currentId = ''
    sections.forEach(function (s) {
      if (s.offsetTop <= scrollPos) {
        currentId = s.id
      }
    })
    navLinks.forEach(function (a) {
      a.classList.remove('active')
      if (a.getAttribute('href') === '#' + currentId) {
        a.classList.add('active')
      }
    })
  }

  window.addEventListener('scroll', updateActiveLink, { passive: true })
  updateActiveLink()

  // ── Smooth scroll for nav clicks ───────────────────
  navLinks.forEach(function (a) {
    a.addEventListener('click', function (e) {
      var href = a.getAttribute('href')
      if (href && href.startsWith('#')) {
        e.preventDefault()
        var target = document.getElementById(href.slice(1))
        if (target) {
          target.scrollIntoView({ behavior: 'smooth', block: 'start' })
        }
        if (window.innerWidth <= 768) {
          sidebar.classList.remove('open')
        }
      }
    })
  })

  // ── Simple syntax highlighting for <pre><code> ─────
  function highlightCode () {
    document.querySelectorAll('pre code').forEach(function (block) {
      var html = block.innerHTML
      html = html
        .replace(/(\/\/.*$)/gm, '<span class="cm">$1</span>')
        .replace(/\b(import|from|def|class|return|if|elif|else|for|while|in|not|and|or|async|await|with|as|try|except|finally|raise|pass|break|continue|True|False|None|self|print|yield|lambda|global|nonlocal|del)\b/g, '<span class="kw">$1</span>')
        .replace(/\b(\d+\.?\d*)\b/g, '<span class="num">$1</span>')
        .replace(/'[^']*'/g, '<span class="str">$1</span>')
        .replace(/"[^"]*"/g, '<span class="str">$1</span>')
        .replace(/(def |class )(\w+)/g, '$1<span class="fn">$2</span>')
      block.innerHTML = html
    })
  }

  highlightCode()
})()
