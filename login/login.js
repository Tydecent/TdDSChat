/* 登录页交互：原生 JS，无框架依赖。表单内提示错误，请求中禁用按钮。 */
;(function () {
  'use strict'

  var form = document.getElementById('login-form')
  var usernameInput = document.getElementById('username')
  var passwordInput = document.getElementById('password')
  var errorBox = document.getElementById('login-error')
  var submit = document.getElementById('submit')

  /** 只接受站内相对路径，避免开放重定向 */
  function safeNext() {
    var raw = new URLSearchParams(window.location.search).get('next')
    if (!raw) return '/'
    if (raw.charAt(0) !== '/' || raw.indexOf('//') === 0) return '/'
    return raw
  }

  function showError(message) {
    errorBox.textContent = message
    errorBox.hidden = false
    usernameInput.setAttribute('aria-invalid', 'true')
    passwordInput.setAttribute('aria-invalid', 'true')
  }

  function clearError() {
    errorBox.hidden = true
    errorBox.textContent = ''
    usernameInput.removeAttribute('aria-invalid')
    passwordInput.removeAttribute('aria-invalid')
  }

  function setLoading(loading) {
    submit.disabled = loading
    submit.classList.toggle('is-loading', loading)
    submit.setAttribute('aria-busy', loading ? 'true' : 'false')
    usernameInput.readOnly = loading
    passwordInput.readOnly = loading
  }

  function messageFromResponse(status, body) {
    if (body && typeof body.message === 'string' && body.message) return body.message
    if (status === 401) return '用户名或密码错误'
    if (status === 429) return '尝试次数过多，请稍后再试'
    if (status >= 500) return '服务暂时不可用，请稍后重试'
    return '登录失败，请重试'
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault()
    if (submit.disabled) return

    var username = usernameInput.value.trim()
    var password = passwordInput.value

    clearError()
    if (!username || !password) {
      showError('请输入账号和密码')
      ;(!username ? usernameInput : passwordInput).focus()
      return
    }

    setLoading(true)

    fetch('/api/auth/login', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ username: username, password: password }),
    })
      .then(function (response) {
        return response
          .json()
          .catch(function () {
            return null
          })
          .then(function (body) {
            if (!response.ok) {
              throw new Error(messageFromResponse(response.status, body))
            }
            return body
          })
      })
      .then(function () {
        window.location.replace(safeNext())
      })
      .catch(function (error) {
        setLoading(false)
        var message = error && error.message ? error.message : '网络异常，请检查连接后重试'
        if (message === 'Failed to fetch') message = '网络异常，请检查连接后重试'
        showError(message)
        passwordInput.select()
      })
  })

  // 输入时立即清除上一次的错误提示
  ;[usernameInput, passwordInput].forEach(function (input) {
    input.addEventListener('input', function () {
      if (!errorBox.hidden) clearError()
    })
  })

  usernameInput.focus()
})()
