function showLoading(message, subtitle = '') {
  hideLoading();

  const modal = document.createElement('div');
  modal.id = 'loadingModal';
  modal.className = 'loading-overlay';
  modal.innerHTML = `
    <div class="loading-content">
      <div class="spinner"></div>
      <p>${message}</p>
      ${subtitle ? `<p class="subtitle">${subtitle}</p>` : ''}
    </div>
  `;
  document.body.appendChild(modal);
}

function hideLoading() {
  const modal = document.getElementById('loadingModal');
  if (modal) {
    modal.remove();
  }
}

document.getElementById('loginForm').addEventListener('submit', async function (event) {
  event.preventDefault();

  const raw  = document.getElementById('username').value.trim().toUpperCase();
  const salt = document.getElementById('password').value;
  const role = document.getElementById('role').value;

  console.log("🔸 Identity:", raw);
  console.log("🔸 Salt:", salt);
  console.log("🔸 Role:", role);

  const match = raw.match(/^(BV\.[A-Z0-9]+):([A-Z0-9]{8})$/);
  if (!match) {
    alert('Username format: BV.<HOSPITAL>:<8-char-DOCTOR-ID>\nExample: BV.HOSPA:A3X7K2PQ');
    document.getElementById('username').focus();
    return;
  }
  const [, hospital, doctorId] = match;

  showLoading(
    'Authenticating...',
    'Generating zero-knowledge proof (this may take 5-10 seconds)'
  );

  try {
    const loginRes = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hospital, doctorId, salt, role })
    });

    if (!loginRes.ok) {
      const errorData = await loginRes.json();
      throw new Error(errorData.error || `Login failed: ${loginRes.status}`);
    }

    const result = await loginRes.json();
    console.log('✅ Login result:', result);

    hideLoading();

    alert("Login successful!");

    if (result.isDoctor) {
      window.location.href = `doctorpage.html?hashId=${result.hashIdHex}`;
    } else {
      window.location.href = `patientpage.html?hashId=${result.hashIdHex}`;
      console.log('goto patientpage.html?hashId=', result.hashIdHex);
    }

  } catch (err) {
    hideLoading();
    console.error('❌ Error during login:', err);
    alert(`Login failed: ${err.message}\n\nPlease check your credentials and try again.`);
  }
});


function togglePassword() {
  const passwordInput = document.getElementById('password');
  const eyeOpen = document.getElementById('eyeOpen');
  const eyeClosed = document.getElementById('eyeClosed');
  if (passwordInput.type === 'password') {
    passwordInput.type = 'text';
    eyeOpen.style.display = 'inline-block';
    eyeClosed.style.display = 'none';
  } else {
    passwordInput.type = 'password';
    eyeOpen.style.display = 'none';
    eyeClosed.style.display = 'inline-block';
  }
}