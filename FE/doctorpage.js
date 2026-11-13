const doctorInfo = {
  hashId: '12345ABCDE67890...',
  department: 'Cardiology',
  departmentHash: 'ABCDE12345F...',
};

const patients = [
  { hash: 'A1B2C3D4E5F6...' },
  { hash: 'B9C8D7E6F5A4...' },
  { hash: 'F3E2D1C0B9A8...' },
  { hash: 'E5F4G3H2J1K0...' },
  { hash: '98765ZXCVB...' },
  { hash: 'ADHDAIWJD5F6...' },
  { hash: 'A1BƯUASDIYF6...' },
];
function renderPatients(filter = '') {
  const list = document.getElementById('patients-list');
  list.innerHTML = '';
  let count = 0;
  patients.forEach((p, i) => {
    const num = i + 1;
    if (
      filter === '' ||
      (`#${num}`.indexOf(filter.toLowerCase()) !== -1) ||
      (p.hash.toLowerCase().indexOf(filter.toLowerCase()) !== -1)
    ) {
      count++;
      const li = document.createElement('li');
      li.innerHTML = `
        <span class="patient-label">#${num} Patient</span>
        <span class="patient-hash">${p.hash}</span>
      `;
      list.appendChild(li);
    }
  });
  if (count === 0) {
    const li = document.createElement('li');
    li.innerHTML = `<span style="color:#c00;font-weight:bold;">No matching patient found.</span>`;
    list.appendChild(li);
  }
}

renderPatients();

document.getElementById('search-patient').addEventListener('input', function() {
  renderPatients(this.value.trim());
});

document.getElementById('doctor-hash').textContent = doctorInfo.hashId;
document.getElementById('department-name').textContent = doctorInfo.department;

let showDepartmentPlain = true;
function toggleDepartmentHash() {
  showDepartmentPlain = !showDepartmentPlain;
  document.getElementById('department-name').textContent =
    showDepartmentPlain ? doctorInfo.department : doctorInfo.departmentHash;
}

const list = document.getElementById('patients-list');
patients.forEach((p, i) => {
  const li = document.createElement('li');
  li.innerHTML = `
    <span class="patient-label">#${i + 1} Patient</span>
    <span class="patient-hash">${p.hash}</span>
  `;
  list.appendChild(li);
});

function openPatientPage(patientHash) {
  window.location.href = `patientpage.html?patient=${encodeURIComponent(patientHash)}&role=doctor&doctorDept=${encodeURIComponent(currentDoctorDept)}`;
}


document.querySelectorAll('.dept-label').forEach(label => {
  label.style.cursor = 'pointer';
  label.addEventListener('click', function() {
    window.location.href = `medicalrecords.html?patient=${encodeURIComponent(currentPatientHash)}&department=${encodeURIComponent(this.textContent)}&role=${currentRole}&doctorDept=${encodeURIComponent(doctorDepartment)}`;
  });
});

