// Lấy param từ URL
function getParam(key) {
  return new URLSearchParams(window.location.search).get(key);
}
const patientHash = getParam('patient') || 'Unknown';
const department = getParam('department') || 'Unknown';

// Giả lập danh sách hồ sơ
const records = [
  { title: "Phẫu thuật ruột thừa", date: "2024-06-01", note: "Thành công", file: "appendix.pdf" },
  { title: "Nội soi dạ dày", date: "2024-05-25", note: "Bình thường", file: "gastroscopy.pdf" }
];
// Giả lập phân quyền: 
const loginRole = getParam('role') || 'patient'; // 'doctor' hoặc 'patient'
const doctorDept = getParam('doctorDept') || ''; // vd: Surgery

document.getElementById('patientHash').textContent = patientHash;
document.getElementById('departmentName').textContent = department;

// Hiển thị nút upload nếu là bác sĩ đúng khoa
if (loginRole === 'doctor' && doctorDept === department) {
  document.getElementById('uploadBtnWrapper').innerHTML = `<button id="uploadRecordBtn">Upload record</button>`;
} else {
  document.getElementById('uploadBtnWrapper').innerHTML = '';
}

// Render hồ sơ
const list = document.getElementById('recordList');
if (!records.length) {
  list.innerHTML = `<li class="no-record">No medical records found.</li>`;
} else {
  records.forEach(r => {
    const li = document.createElement('li');
    li.innerHTML = `<b>${r.title}</b> (${r.date})<br/><span>${r.note}</span> <a href="${r.file}" target="_blank">[File]</a>`;
    list.appendChild(li);
  });
}
