// === DEMO: Dữ liệu bác sĩ và danh sách bệnh nhân ===
// Ở thực tế bạn lấy từ localStorage hoặc backend.
const doctorInfo = {
  hashId: '12345ABCDE67890...',
  department: 'Cardiology',
  departmentHash: 'ABCDE12345F...',
};

// Danh sách bệnh nhân demo
const patients = [
  { hash: 'A1B2C3D4E5F6...' },
  { hash: 'B9C8D7E6F5A4...' },
  { hash: 'F3E2D1C0B9A8...' },
  { hash: 'E5F4G3H2J1K0...' },
  { hash: '98765ZXCVB...' },
  { hash: 'ADHDAIWJD5F6...' },
  { hash: 'A1BƯUASDIYF6...' },
];
// --- Render danh sách (thêm hàm renderPatients) ---
function renderPatients(filter = '') {
  const list = document.getElementById('patients-list');
  list.innerHTML = '';
  let count = 0;
  patients.forEach((p, i) => {
    const num = i + 1;
    // So khớp theo số thứ tự hoặc hashId (không phân biệt hoa thường)
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
  // Nếu không có kết quả
  if (count === 0) {
    const li = document.createElement('li');
    li.innerHTML = `<span style="color:#c00;font-weight:bold;">No matching patient found.</span>`;
    list.appendChild(li);
  }
}

// --- Render initial ---
renderPatients();

// --- Xử lý search realtime ---
document.getElementById('search-patient').addEventListener('input', function() {
  renderPatients(this.value.trim());
});

// === Render thông tin bác sĩ ===
document.getElementById('doctor-hash').textContent = doctorInfo.hashId;
document.getElementById('department-name').textContent = doctorInfo.department;

// Trạng thái hiển thị department (rõ ràng/hash)
let showDepartmentPlain = true;
function toggleDepartmentHash() {
  showDepartmentPlain = !showDepartmentPlain;
  document.getElementById('department-name').textContent =
    showDepartmentPlain ? doctorInfo.department : doctorInfo.departmentHash;
}

// === Render danh sách bệnh nhân ===
const list = document.getElementById('patients-list');
patients.forEach((p, i) => {
  const li = document.createElement('li');
  li.innerHTML = `
    <span class="patient-label">#${i + 1} Patient</span>
    <span class="patient-hash">${p.hash}</span>
  `;
  list.appendChild(li);
});

// Giả sử khi click vào 1 bệnh nhân trong danh sách
function openPatientPage(patientHash) {
  window.location.href = `patientpage.html?patient=${encodeURIComponent(patientHash)}&role=doctor&doctorDept=${encodeURIComponent(currentDoctorDept)}`;
}

// Ở patientpage, khi click vào department
// Đặt đoạn sau sau khi render danh sách department:
// const currentPatientHash =  /* lấy hash bệnh nhân hiện tại */;
// const currentRole = 'doctor';
// const doctorDepartment =  /* khoa của bác sĩ đăng nhập */;

document.querySelectorAll('.dept-label').forEach(label => {
  label.style.cursor = 'pointer';
  label.addEventListener('click', function() {
    window.location.href = `medicalrecords.html?patient=${encodeURIComponent(currentPatientHash)}&department=${encodeURIComponent(this.textContent)}&role=${currentRole}&doctorDept=${encodeURIComponent(doctorDepartment)}`;
  });
});

