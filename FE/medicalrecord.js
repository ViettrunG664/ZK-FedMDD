function getParam(key) {
  return new URLSearchParams(window.location.search).get(key);
}
const patientHash = getParam('patient') || 'Unknown';
const department = getParam('department') || 'Unknown';

const records = [
  { title: "Appendectomy", date: "2024-06-01", note: "Successful", file: "appendix.pdf" },
  { title: "Gastroscopy", date: "2024-05-25", note: "Normal", file: "gastroscopy.pdf" }
];
const loginRole = getParam('role') || 'patient';
const doctorDept = getParam('doctorDept') || '';

document.getElementById('patientHash').textContent = patientHash;
document.getElementById('departmentName').textContent = department;

if (loginRole === 'doctor' && doctorDept === department) {
  document.getElementById('uploadBtnWrapper').innerHTML = `<button id="uploadRecordBtn">Upload record</button>`;
} else {
  document.getElementById('uploadBtnWrapper').innerHTML = '';
}

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
