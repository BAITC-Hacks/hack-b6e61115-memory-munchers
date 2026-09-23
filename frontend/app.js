const WEATHER_API_URL = 'https://localhost:7147/WeatherForecast';
const refreshButton = document.querySelector('#refresh-button');
const rows = document.querySelector('#forecast-rows');
const status = document.querySelector('#status');
const updatedLabel = document.querySelector('#updated-label');

const icons = {
  freezing: '❄', bracing: '🌬', chilly: '☁', cool: '⛅', mild: '🌤',
  warm: '☀', balmy: '🌞', hot: '☀', sweltering: '🔥', scorching: '☀'
};
const temperatureNotes = [
  { max: 0, text: 'Bundle up' }, { max: 10, text: 'Crisp air' },
  { max: 20, text: 'Just right' }, { max: 30, text: 'Lovely' },
  { max: 40, text: 'Sunny side' }, { max: Infinity, text: 'Take it easy' }
];

function dateParts(value) {
  const date = new Date(`${value}T12:00:00`);
  return {
    weekday: new Intl.DateTimeFormat(undefined, { weekday: 'long' }).format(date),
    compact: new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(date)
  };
}

function renderForecast(forecast) {
  rows.replaceChildren();
  if (!Array.isArray(forecast) || forecast.length === 0) {
    rows.innerHTML = '<tr><td class="empty-cell" colspan="5">No forecast days were returned.</td></tr>';
    return;
  }

  forecast.forEach((day, index) => {
    const { weekday, compact } = dateParts(day.date);
    const summary = day.summary || 'Unknown';
    const celsius = Number(day.temperatureC);
    const fahrenheit = Number(day.temperatureF ?? Math.round((celsius * 9) / 5 + 32));
    const note = temperatureNotes.find(({ max }) => celsius <= max).text;
    const progress = Math.max(8, Math.min(100, ((celsius + 20) / 75) * 100));
    const icon = icons[summary.toLowerCase()] || '🌥';
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><div class="day-cell"><span class="weather-icon" aria-hidden="true">${icon}</span><div><div class="day-name">${index === 0 ? 'Tomorrow' : weekday}</div><div class="day-date">${compact}</div></div></div></td>
      <td class="condition">${escapeHtml(summary)}</td>
      <td><span class="temperature">${celsius}°<small>C</small></span></td>
      <td><span class="feels">${fahrenheit}°F</span></td>
      <td><div class="outlook"><div class="outlook-track"><div class="outlook-fill" style="width:${progress}%"></div></div><span class="outlook-label">${note}</span></div></td>`;
    rows.append(tr);
  });
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
}

async function loadForecast() {
  refreshButton.disabled = true;
  status.classList.remove('error');
  status.innerHTML = '<span class="spinner"></span> Gathering the latest forecast…';
  try {
    const response = await fetch(WEATHER_API_URL);
    if (!response.ok) throw new Error(`The API replied with ${response.status} ${response.statusText}.`);
    const forecast = await response.json();
    renderForecast(forecast);
    status.textContent = '';
    updatedLabel.textContent = `Updated ${new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(new Date())}`;
  } catch (error) {
    rows.innerHTML = '<tr><td class="empty-cell" colspan="5">The forecast could not be loaded. Check the API URL and make sure the ASP.NET app is running.</td></tr>';
    status.textContent = error instanceof TypeError ? 'Could not reach the API. Check its URL and CORS settings.' : error.message;
    status.classList.add('error');
    updatedLabel.textContent = 'Connection unavailable';
  } finally {
    refreshButton.disabled = false;
  }
}

document.querySelector('#today-label').textContent = new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric' }).format(new Date()).toUpperCase();
refreshButton.addEventListener('click', loadForecast);
loadForecast();
