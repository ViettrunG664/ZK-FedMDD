function getQueryParam(name) {
  const url = new URL(window.location.href);
  return url.searchParams.get(name) || '';
}
const hashId = getQueryParam('hashId'); // Lấy hashId từ query
const order = getQueryParam('order') || 2; // nếu cần thêm thứ tự

// Demo: danh sách khoa của bệnh nhân (nên lấy từ backend nếu có)
const departments = [
  { name: "Dental", records: 1 },
  { name: "Surgery", records: 0 },
  { name: "Gastroenterology", records: 2 },
  { name: "Cardiology", records: 3 },
  { name: "Oncology", records: 0 },
  { name: "Pediatrics", records: 0 },
  { name: "Neurology", records: 1 },
  { name: "Radiology", records: 0 }
];

function shortenHash(x) {
  if (!x|| x.length < 12) return x;
  return x.slice(0, 10) + "..." + x.slice(-6);
}

// ==== Render thông tin bệnh nhân ====
document.getElementById('patient-hash').textContent = shortenHash(hashId);
document.getElementById('patient-hash').title = hashId;
document.getElementById('patient-order').textContent = '#' + order;


// Hàm render danh sách khoa
function renderDepartments(filter = '') {
  const list = document.getElementById('departments-list');
  list.innerHTML = '';
  let count = 0;
  departments.forEach((dept) => {
    if (
      filter === '' ||
      dept.name.toLowerCase().includes(filter.toLowerCase())
    ) {
      count++;
      const li = document.createElement('li');
      li.innerHTML = `
        <span class="dept-label">${dept.name}</span>
        <span class="record-count">${dept.records}</span>
      `;
      list.appendChild(li);
    }
  });
  if (count === 0) {
    const li = document.createElement('li');
    li.innerHTML = `<span style="color:#c00;font-weight:bold;">No department found.</span>`;
    list.appendChild(li);
  }
}
renderDepartments();
const currentPatientHash = hashId; // Giả sử patientInfo là object lưu hashID
const currentRole = 'patient';
const doctorDepartment = ''; // Nếu không cần truyền

document.querySelectorAll('.dept-label').forEach(label => {
  label.style.cursor = 'pointer'; // Cho dễ nhận biết có thể click
  label.addEventListener('click', function() {
    window.location.href = `medicalrecord.html?patient=${encodeURIComponent(currentPatientHash)}&department=${encodeURIComponent(this.textContent)}&role=${currentRole}&doctorDept=${doctorDepartment}`;
  });
});
// Xử lý search realtime
document.getElementById('search-department').addEventListener('input', function() {
  renderDepartments(this.value.trim());
});
