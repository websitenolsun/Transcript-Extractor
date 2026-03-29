/**
 * Video Transcriber — Frontend Logic
 * Handles chunked uploads, SSE progress, and transcript display.
 */
 
const CHUNK_SIZE = 10 * 1024 * 1024; // 10 MB
const MAX_FILE_SIZE = 5 * 1024 * 1024 * 1024; // 5 GB
 
const ALLOWED_TYPES = [
    'video/mp4', 'video/webm', 'video/avi', 'video/mov',
    'video/mkv', 'video/x-matroska', 'video/quicktime',
    'video/x-msvideo', 'video/x-ms-wmv', 'video/mpeg',
    'audio/mpeg', 'audio/wav', 'audio/mp3', 'audio/ogg',
    'audio/flac', 'audio/x-wav', 'audio/mp4',
];
 
const ALLOWED_EXTENSIONS = [
    '.mp4', '.webm', '.avi', '.mov', '.mkv', '.wmv', '.mpeg',
    '.mpg', '.flv', '.m4v', '.3gp',
    '.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac', '.wma',
];
 
// ── State ───────────────────────────────────────────────────────────────
let selectedFile = null;
let currentJobId = null;
let eventSource = null;
 
// ── DOM Elements ────────────────────────────────────────────────────────
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const fileInfo = document.getElementById('file-info');
const fileName = document.getElementById('file-name');
const fileSize = document.getElementById('file-size');
const fileRemove = document.getElementById('file-remove');
const btnUpload = document.getElementById('btn-upload');
const progressSection = document.getElementById('progress-section');
const progressBar = document.getElementById('progress-bar');
const progressValue = document.getElementById('progress-value');
const progressLabel = document.getElementById('progress-label');
const progressMessage = document.getElementById('progress-message');
const resultSection = document.getElementById('result-section');
const transcriptBox = document.getElementById('transcript-box');
const resultLanguage = document.getElementById('result-language');
const resultDuration = document.getElementById('result-duration');
const resultSegments = document.getElementById('result-segments');
const errorSection = document.getElementById('error-section');
const btnNew = document.getElementById('btn-new');
 
// ── UUID Helper (HTTP uyumlu) ────────────────────────────────────────────
function generateId() {
    return 'xxxxxxxxxxxxxxxx'.replace(/[x]/g, function() {
        return Math.floor(Math.random() * 16).toString(16);
    });
}
 
// ── File Selection ──────────────────────────────────────────────────────
dropZone.addEventListener('click', () => fileInput.click());
 
dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('drag-over');
});
 
dropZone.addEventListener('dragleave', () => {
    dropZone.classList.remove('drag-over');
});
 
dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    const files = e.dataTransfer.files;
    if (files.length > 0) handleFileSelect(files[0]);
});
 
fileInput.addEventListener('change', () => {
    if (fileInput.files.length > 0) handleFileSelect(fileInput.files[0]);
});
 
fileRemove.addEventListener('click', () => clearFile());
 
function handleFileSelect(file) {
    // Validate extension
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
        showError('Desteklenmeyen dosya formatı. Lütfen bir video veya ses dosyası yükleyin.');
        return;
    }
 
    // Validate size
    if (file.size > MAX_FILE_SIZE) {
        showError('Dosya boyutu 5 GB sınırını aşıyor.');
        return;
    }
 
    if (file.size === 0) {
        showError('Dosya boş görünüyor.');
        return;
    }
 
    selectedFile = file;
    fileName.textContent = file.name;
    fileSize.textContent = formatSize(file.size);
 
    dropZone.classList.add('hidden');
    fileInfo.classList.add('visible');
    btnUpload.classList.add('visible');
    hideError();
}
 
function clearFile() {
    selectedFile = null;
    fileInput.value = '';
    dropZone.classList.remove('hidden');
    fileInfo.classList.remove('visible');
    btnUpload.classList.remove('visible');
    hideError();
}
 
// ── Upload ──────────────────────────────────────────────────────────────
btnUpload.addEventListener('click', () => startUpload());
 
async function startUpload() {
    if (!selectedFile) return;
 
    const file = selectedFile;
    const uploadId = generateId();
    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
 
    // Disable UI
    btnUpload.disabled = true;
    btnUpload.textContent = 'Yükleniyor...';
    fileRemove.style.display = 'none';
 
    // Show progress
    progressSection.classList.add('visible');
    progressLabel.textContent = 'Yükleniyor';
    progressMessage.innerHTML = '<span class="spinner"></span> Video yükleniyor...';
 
    try {
        // Upload chunks
        for (let i = 0; i < totalChunks; i++) {
            const start = i * CHUNK_SIZE;
            const end = Math.min(start + CHUNK_SIZE, file.size);
            const chunk = file.slice(start, end);
 
            const formData = new FormData();
            formData.append('chunk', chunk);
            formData.append('upload_id', uploadId);
            formData.append('chunk_index', i);
            formData.append('total_chunks', totalChunks);
            formData.append('filename', file.name);
            formData.append('total_size', file.size);
 
            const response = await fetch('/upload/chunk', {
                method: 'POST',
                body: formData,
            });
 
            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.error || 'Yükleme hatası');
            }
 
            // Update upload progress
            const uploadProgress = ((i + 1) / totalChunks) * 100;
            updateProgress(uploadProgress, 'Yükleniyor');
            progressMessage.innerHTML = `<span class="spinner"></span> Video yükleniyor... ${formatSize(end)} / ${formatSize(file.size)}`;
        }
 
        // Complete upload
        progressMessage.innerHTML = '<span class="spinner"></span> Dosya birleştiriliyor...';
 
        const completeResponse = await fetch('/upload/complete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                upload_id: uploadId,
                filename: file.name,
                total_chunks: totalChunks,
            }),
        });
 
        if (!completeResponse.ok) {
            const err = await completeResponse.json();
            throw new Error(err.error || 'Yükleme tamamlama hatası');
        }
 
        const result = await completeResponse.json();
        currentJobId = result.job_id;
 
        // Start listening for transcription progress
        progressLabel.textContent = 'Transkripsiyon';
        progressMessage.innerHTML = '<span class="spinner"></span> Transkripsiyon başlatılıyor...';
        updateProgress(0);
        startSSE(currentJobId);
 
    } catch (err) {
        showError(err.message);
        resetUploadUI();
    }
}
 
// ── SSE Progress ────────────────────────────────────────────────────────
function startSSE(jobId) {
    if (eventSource) eventSource.close();
 
    eventSource = new EventSource(`/status/${jobId}`);
 
    eventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);
 
        updateProgress(data.progress, 'Transkripsiyon');
        progressMessage.innerHTML = `<span class="spinner"></span> ${data.message}`;
 
        if (data.status === 'completed') {
            eventSource.close();
            eventSource = null;
            onTranscriptionComplete(jobId);
        } else if (data.status === 'failed') {
            eventSource.close();
            eventSource = null;
            showError(data.error || 'Transkripsiyon başarısız oldu.');
            resetUploadUI();
        }
    };
 
    eventSource.onerror = () => {
        eventSource.close();
        eventSource = null;
        showError('Sunucu bağlantısı kesildi.');
        resetUploadUI();
    };
}
 
// ── Complete ────────────────────────────────────────────────────────────
async function onTranscriptionComplete(jobId) {
    try {
        const response = await fetch(`/result/${jobId}`);
        if (!response.ok) throw new Error('Sonuç alınamadı');
 
        const data = await response.json();
 
        // Hide progress
        progressSection.classList.remove('visible');
 
        // Show result
        transcriptBox.textContent = data.text;
        resultLanguage.textContent = data.language.toUpperCase();
        resultDuration.textContent = formatDuration(data.duration);
        resultSegments.textContent = data.segments.length;
 
        resultSection.classList.add('visible');
        btnNew.classList.add('visible');
 
    } catch (err) {
        showError(err.message);
    }
}
 
// ── Actions ─────────────────────────────────────────────────────────────
function copyTranscript() {
    const text = transcriptBox.textContent;
    navigator.clipboard.writeText(text).then(() => {
        const btn = document.querySelector('.btn-copy');
        const original = btn.innerHTML;
        btn.innerHTML = '✅ Kopyalandı!';
        setTimeout(() => btn.innerHTML = original, 2000);
    });
}
 
function downloadTranscript(format) {
    if (!currentJobId) return;
    window.open(`/download/${currentJobId}/${format}`, '_blank');
}
 
function startNewTranscription() {
    // Reset all
    currentJobId = null;
    selectedFile = null;
    fileInput.value = '';
 
    dropZone.classList.remove('hidden');
    fileInfo.classList.remove('visible');
    btnUpload.classList.remove('visible');
    btnUpload.disabled = false;
    btnUpload.textContent = '🚀 Transkripsiyon Başlat';
    if (fileRemove) fileRemove.style.display = '';
 
    progressSection.classList.remove('visible');
    resultSection.classList.remove('visible');
    btnNew.classList.remove('visible');
    hideError();
 
    updateProgress(0);
}
 
// ── Helpers ─────────────────────────────────────────────────────────────
function updateProgress(percent, label) {
    progressBar.style.width = `${Math.min(percent, 100)}%`;
    progressValue.textContent = `${Math.round(percent)}%`;
    if (label) progressLabel.textContent = label;
}
 
function formatSize(bytes) {
    if (bytes === 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    const k = 1024;
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + units[i];
}
 
function formatDuration(seconds) {
    const hrs = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
 
    if (hrs > 0) return `${hrs}s ${mins}dk ${secs}sn`;
    if (mins > 0) return `${mins}dk ${secs}sn`;
    return `${secs}sn`;
}
 
function showError(msg) {
    errorSection.textContent = '⚠️ ' + msg;
    errorSection.classList.add('visible');
}
 
function hideError() {
    errorSection.classList.remove('visible');
}
 
function resetUploadUI() {
    btnUpload.disabled = false;
    btnUpload.textContent = '🚀 Transkripsiyon Başlat';
    if (fileRemove) fileRemove.style.display = '';
    progressSection.classList.remove('visible');
    btnNew.classList.add('visible');
}
 