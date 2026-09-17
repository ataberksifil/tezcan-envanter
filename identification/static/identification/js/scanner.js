(function () {
    "use strict";

    const startButton = document.getElementById("scanner-start");
    if (!startButton) return;

    const stopButton = document.getElementById("scanner-stop");
    const retryButton = document.getElementById("scanner-retry");
    const videoWrap = document.getElementById("scanner-video-wrap");
    const video = document.getElementById("scanner-video");
    const status = document.getElementById("scanner-status");
    const form = document.getElementById("scan-resolve-form");
    const payload = document.getElementById("id_payload");
    let controls = null;
    let submitting = false;

    function setStatus(message, isError) {
        status.textContent = message;
        status.classList.toggle("text-danger", Boolean(isError));
        status.classList.toggle("text-body-secondary", !isError);
    }

    function stopScanner() {
        if (controls) {
            controls.stop();
            controls = null;
        }
        if (video.srcObject) {
            video.srcObject.getTracks().forEach(function (track) { track.stop(); });
            video.srcObject = null;
        }
        videoWrap.classList.add("d-none");
        stopButton.classList.add("d-none");
        if (!submitting) startButton.classList.remove("d-none");
    }

    function friendlyCameraError(error) {
        if (error && (error.name === "NotAllowedError" || error.name === "SecurityError")) {
            return "Kamera izni verilmedi. Tarayıcı ayarlarından izin verebilir veya kodu elle / USB okuyucu ile girebilirsiniz.";
        }
        if (error && (error.name === "NotFoundError" || error.name === "DevicesNotFoundError")) {
            return "Kullanılabilir kamera bulunamadı. Kodu elle veya USB okuyucu ile girebilirsiniz.";
        }
        return "Kamera başlatılamadı. Tekrar deneyin veya kodu elle / USB okuyucu ile girin.";
    }

    function buildReader() {
        const reader = new window.ZXingBrowser.BrowserMultiFormatReader(undefined, {
            delayBetweenScanAttempts: 200,
            delayBetweenScanSuccess: 1000
        });
        const formats = window.ZXingBrowser.BarcodeFormat;
        if (formats && formats.QR_CODE !== undefined && formats.CODE_128 !== undefined) {
            reader.possibleFormats = [formats.QR_CODE, formats.CODE_128];
        }
        return reader;
    }

    async function startScanner() {
        submitting = false;
        retryButton.classList.add("d-none");
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            setStatus("Bu tarayıcı kamera erişimini desteklemiyor. Kodu elle veya USB okuyucu ile girebilirsiniz.", true);
            retryButton.classList.remove("d-none");
            return;
        }
        if (!window.ZXingBrowser || !window.ZXingBrowser.BrowserMultiFormatReader) {
            setStatus("Tarayıcı bileşeni yüklenemedi. Kodu elle veya USB okuyucu ile girebilirsiniz.", true);
            retryButton.classList.remove("d-none");
            return;
        }

        startButton.classList.add("d-none");
        stopButton.classList.remove("d-none");
        videoWrap.classList.remove("d-none");
        setStatus("Kamera izni bekleniyor…", false);

        try {
            const reader = buildReader();
            controls = await reader.decodeFromConstraints(
                { audio: false, video: { facingMode: { ideal: "environment" } } },
                video,
                function (result, _error, callbackControls) {
                    if (!result || submitting) return;
                    submitting = true;
                    setStatus("Kod okundu; kayıt açılıyor…", false);
                    payload.value = result.getText();
                    callbackControls.stop();
                    controls = null;
                    form.requestSubmit();
                }
            );
            setStatus("Code 128 veya QR kodunu kameraya gösterin.", false);
        } catch (error) {
            stopScanner();
            setStatus(friendlyCameraError(error), true);
            retryButton.classList.remove("d-none");
        }
    }

    startButton.addEventListener("click", startScanner);
    retryButton.addEventListener("click", startScanner);
    stopButton.addEventListener("click", function () {
        stopScanner();
        setStatus("Kamera durduruldu.", false);
        retryButton.classList.remove("d-none");
    });
    window.addEventListener("pagehide", stopScanner);
}());
