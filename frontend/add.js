const WEATHER_API_URL = 'https://localhost:7147/WeatherForecast';
const form = document.querySelector('#forecast-form');
const status = document.querySelector('#form-status');
const submitButton = form.querySelector('button[type="submit"]');
const dateInput = form.elements.date;

dateInput.min = new Date().toISOString().slice(0, 10);

form.addEventListener('submit', async event => {
  event.preventDefault();
  status.classList.remove('error', 'success');
  status.textContent = 'Saving your forecast…';
  submitButton.disabled = true;

  const forecast = {
    date: dateInput.value,
    temperatureC: Number(form.elements.temperatureC.value),
    summary: form.elements.summary.value
  };

  try {
    const response = await fetch(WEATHER_API_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(forecast)
    });
    if (!response.ok) throw new Error(`The API replied with ${response.status} ${response.statusText}.`);
    status.textContent = 'Forecast saved. Returning to the outlook…';
    status.classList.add('success');
    window.setTimeout(() => { window.location.href = './index.html'; }, 800);
  } catch (error) {
    status.textContent = error instanceof TypeError
      ? 'Could not reach the API. Check its URL and CORS settings.'
      : error.message;
    status.classList.add('error');
    submitButton.disabled = false;
  }
});
