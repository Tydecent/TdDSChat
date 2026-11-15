(function () {
  'use strict';

  // DOM 元素
  const form = document.getElementById('login-form');
  const usernameInput = document.getElementById('username');
  const passwordInput = document.getElementById('password');
  const submitBtn = document.getElementById('submit-btn');
  const errorBox = document.getElementById('login-error');
  const togglePassword = document.getElementById('togglePassword');
  const eyeIconSvg = togglePassword.querySelector('svg');

  // 小眼睛图标路径
  const eyeClosePath = `<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line>`;
  const eyeOpenPath = `<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle>`;

  // 1. 密码显隐切换
  togglePassword.addEventListener('click', function () {
    const type = passwordInput.getAttribute('type') === 'password' ? 'text' : 'password';
    passwordInput.setAttribute('type', type);
    eyeIconSvg.innerHTML = type === 'text' ? eyeOpenPath : eyeClosePath;
  });

  // 2. 错误提示处理
  function showError(message) {
    errorBox.textContent = message;
    errorBox.style.display = 'block';
  }

  function clearError() {
    errorBox.style.display = 'none';
    errorBox.textContent = '';
  }

  // 3. Loading 状态处理
  function setLoading(loading) {
    submitBtn.disabled = loading;
    submitBtn.classList.toggle('is-loading', loading);
    usernameInput.disabled = loading;
    passwordInput.disabled = loading;
  }

  // 输入时清除错误
  [usernameInput, passwordInput].forEach(input => {
    input.addEventListener('input', () => {
      if (errorBox.style.display === 'block') clearError();
    });
  });

  // 4. 表单提交拦截
  form.addEventListener('submit', function (event) {
    event.preventDefault();
    if (submitBtn.disabled) return;

    const username = usernameInput.value.trim();
    const password = passwordInput.value;

    clearError();

    // 前端基础校验
    if (!username || !password) {
      showError('请输入账号和密码');
      (!username ? usernameInput : passwordInput).focus();
      return;
    }

    setLoading(true);

    // ==========================================
    // 这里保留了你原来代码里的 Fetch 请求逻辑
    // 你可以根据实际情况调整 URL 或参数
    // ==========================================
    fetch('/api/auth/login', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ username: username, password: password }),
    })
      .then(async function (response) {
        const body = await response.json().catch(() => null);
        if (!response.ok) {
          throw new Error((body && body.message) || '登录失败，请重试');
        }
        return body;
      })
      .then(function () {
        // 登录成功跳转
        // 这里简单处理，直接跳转首页。如果你有 next 参数，可以像你原代码那样使用 safeNext()
        window.location.replace('/');
      })
      .catch(function (error) {
        setLoading(false);
        let message = error.message || '网络异常，请检查连接后重试';
        if (message === 'Failed to fetch') message = '网络异常，请检查连接后重试';
        showError(message);
        passwordInput.select();
      });

    /* 
    // 如果你只想看前端效果，可以把上面的 fetch 注释掉，解开下面这段代码测试 UI：
    setTimeout(() => {
      setLoading(false);
      showError('用户名或密码错误（测试）');
    }, 1500); 
    */
  });

  // 自动聚焦
  usernameInput.focus();
})();