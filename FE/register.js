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
  if (modal) modal.remove();
}

document.getElementById('registerForm').addEventListener('submit', async function (event) {
  event.preventDefault();
  const raw  = document.getElementById('username').value.trim().toUpperCase();
  const role = document.getElementById('role').value;

  const match = raw.match(/^(BV\.[A-Z0-9]+):([A-Z0-9]{8})$/);
  if (!match) {
    alert('Format: BV.<HOSPITAL>:<8-char-DOCTOR-ID>\nExample: BV.HOSPA:A3X7K2PQ');
    return;
  }
  const [, hospital, doctorId] = match;
  const data = { hospital, doctorId, role };

  showLoading(
    'Creating your account...',
    'Registering on blockchain (may take 3-10 seconds)'
  );

  let result;
  try {
    const res = await fetch('http://localhost:3001/api/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });

    result = await res.json();
    if (!res.ok) throw new Error(result.error || "Register failed");

    hideLoading();
  } catch (e) {
    hideLoading();
    alert(`Registration Error: ${e.message}\n\nPlease try again or contact support.`);
    return;
  }

  document.getElementById('Salt-number').innerText =
    `Registration successful!\nYour "salt number": ${result.salt}\nSave this "Salt number" for future logins.`;

  document.getElementById('Salt-number').setAttribute('data-salt', result.salt);

  document.getElementById('popup').style.display = 'flex';
});


function copySalt() {
  const msg = document.getElementById('Salt-number').innerText;
  navigator.clipboard.writeText(msg);
  alert('Copied!');
}

function downloadCongrats() {
  const msg = document.getElementById('Salt-number').innerText;
  const blob = new Blob([msg], { type: "text/plain;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "salt_number.txt";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

function closePopup() {
  document.getElementById('popup').style.display = 'none';
  window.location.href = 'http://localhost:3000/login.html';
}
