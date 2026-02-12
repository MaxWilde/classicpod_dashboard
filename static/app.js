const form = document.getElementById("mount-form");
const mountpointInput = document.getElementById("mountpoint");
const loadButton = document.getElementById("load-button");
const addForm = document.getElementById("add-form");
const musicFilesInput = document.getElementById("music-files");
const musicFolderInput = document.getElementById("music-folder");
const musicSourceMode = document.getElementById("music-source-mode");
const selectMusicButton = document.getElementById("select-music-button");
const clearSelectionButton = document.getElementById("clear-selection-button");
const selectionSummary = document.getElementById("selection-summary");
const addButton = document.getElementById("add-button");
const statusEl = document.getElementById("status");
const statsEl = document.getElementById("stats");
const browserEl = document.getElementById("browser");
const searchEl = document.getElementById("search");
const sortEl = document.getElementById("sort");
const tracksBody = document.getElementById("tracks-body");
const albumStripEl = document.getElementById("album-strip");
const albumModal = document.getElementById("album-modal");
const albumModalClose = document.getElementById("album-modal-close");
const albumModalCover = document.getElementById("album-modal-cover");
const albumModalTitle = document.getElementById("album-modal-title");
const albumModalMeta = document.getElementById("album-modal-meta");
const albumModalTracks = document.getElementById("album-modal-tracks");

let tracks = [];
let albums = [];
let currentMountpoint = "";
let actionInFlight = false;
let uploadInFlight = false;
const COVER_PLACEHOLDER = "/static/cover-placeholder.svg";
const ALLOWED_AUDIO_EXTENSIONS = new Set([".mp3", ".m4a", ".aac", ".wav", ".aiff", ".aif", ".flac", ".ogg", ".opus", ".m4b"]);

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDuration(totalSeconds) {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(seconds / 60);
  const hrs = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return `${hrs}h ${mins}m`;
}

function formatTrackDuration(totalSeconds) {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const mins = Math.floor(seconds / 60);
  const sec = seconds % 60;
  return `${mins}:${String(sec).padStart(2, "0")}`;
}

function updateStats(data) {
  document.getElementById("track-count").textContent = data.track_count.toLocaleString();
  document.getElementById("artist-count").textContent = data.artist_count.toLocaleString();
  document.getElementById("album-count").textContent = data.album_count.toLocaleString();
  document.getElementById("duration-total").textContent = formatDuration(data.total_duration_seconds);
}

function normalizeAlbumKey(album) {
  return String(album || "Unknown Album")
    .trim()
    .toLowerCase();
}

function coverUrl(track) {
  if (!track.artwork || !track.ipod_path || !currentMountpoint) {
    return COVER_PLACEHOLDER;
  }
  const mountpoint = encodeURIComponent(currentMountpoint);
  const ipodPath = encodeURIComponent(track.ipod_path);
  return `/api/cover?mountpoint=${mountpoint}&ipod_path=${ipodPath}`;
}

function updateWriteUi() {
  musicFilesInput.disabled = uploadInFlight;
  musicFolderInput.disabled = uploadInFlight;
  musicSourceMode.disabled = uploadInFlight;
  selectMusicButton.disabled = uploadInFlight;
  clearSelectionButton.disabled = uploadInFlight;
  addButton.disabled = uploadInFlight;
  addButton.title = "";
}

function isSupportedAudioFile(file) {
  const name = String(file?.name || "").toLowerCase();
  const extIndex = name.lastIndexOf(".");
  if (extIndex === -1) {
    return false;
  }
  return ALLOWED_AUDIO_EXTENSIONS.has(name.slice(extIndex));
}

function collectSelectedFiles() {
  const combined = [...Array.from(musicFilesInput.files || []), ...Array.from(musicFolderInput.files || [])];
  const seen = new Set();
  const deduped = [];
  for (const file of combined) {
    if (!isSupportedAudioFile(file)) {
      continue;
    }
    const rel = file.webkitRelativePath || "";
    const key = `${rel}|${file.name}|${file.size}|${file.lastModified}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    deduped.push(file);
  }
  return deduped;
}

function updateSelectionSummary() {
  const rawCount = [...Array.from(musicFilesInput.files || []), ...Array.from(musicFolderInput.files || [])].length;
  const count = collectSelectedFiles().length;
  const ignored = Math.max(0, rawCount - count);
  if (!count) {
    selectionSummary.textContent = rawCount ? `No supported audio selected. Ignored ${ignored} file(s).` : "No files selected.";
    return;
  }
  selectionSummary.textContent = ignored
    ? `${count} audio file(s) selected. Ignored ${ignored} unsupported file(s).`
    : `${count} file(s) selected.`;
}

function clearSelectedFiles() {
  musicFilesInput.value = "";
  musicFolderInput.value = "";
  updateSelectionSummary();
}

function buildAlbums() {
  const map = new Map();
  for (const track of tracks) {
    const albumName = String(track.album || "Unknown Album").trim() || "Unknown Album";
    const key = normalizeAlbumKey(albumName);
    if (!map.has(key)) {
      map.set(key, {
        key,
        album: albumName,
        representative: track,
        artists: new Set(),
        ipodPaths: new Set(),
        trackCount: 0,
      });
    }

    const item = map.get(key);
    item.trackCount += 1;
    item.artists.add(track.artist || "Unknown Artist");
    if (track.ipod_path) {
      item.ipodPaths.add(track.ipod_path);
    }
    if (track.artwork && !item.representative.artwork) {
      item.representative = track;
    }
  }

  albums = Array.from(map.values())
    .map((album) => ({
      key: album.key,
      album: album.album,
      representative: album.representative,
      artists: Array.from(album.artists),
      ipod_paths: Array.from(album.ipodPaths),
      track_count: album.trackCount,
    }))
    .sort((a, b) => a.album.localeCompare(b.album));
}

function renderAlbumStrip() {
  albumStripEl.innerHTML = albums
    .map((album) => {
      const src = coverUrl(album.representative);
      const label = escapeHtml(album.album || "Unknown Album");
      const artistsLabel = escapeHtml(album.artists.slice(0, 2).join(", "));
      return `
        <article class="album-card" data-album-key="${escapeHtml(album.key)}">
          <button class="album-delete" title="Delete album from iPod" data-album-key="${escapeHtml(album.key)}">x</button>
          <img loading="lazy" src="${src}" alt="${label}" onerror="this.src='${COVER_PLACEHOLDER}'" />
          <div class="album-meta">
            <p title="${label}">${label}</p>
            <p class="album-subtitle" title="${artistsLabel}">${artistsLabel} • ${album.track_count} track(s)</p>
          </div>
        </article>
      `;
    })
    .join("");
}

function applyFilters() {
  const query = searchEl.value.trim().toLowerCase();
  const sort = sortEl.value;
  let filtered = tracks.filter((track) => {
    if (!query) return true;
    const haystack = `${track.title} ${track.artist} ${track.album}`.toLowerCase();
    return haystack.includes(query);
  });

  filtered = filtered.sort((a, b) => {
    if (sort === "year") return b.year - a.year;
    if (sort === "duration") return b.duration_seconds - a.duration_seconds;
    return String(a[sort] || "").localeCompare(String(b[sort] || ""));
  });

  tracksBody.innerHTML = filtered
    .map(
      (track) => `
        <tr>
          <td class="cover-cell"><img class="cover-thumb" loading="lazy" src="${coverUrl(track)}" alt="" onerror="this.src='${COVER_PLACEHOLDER}'" /></td>
          <td>${escapeHtml(track.title)}</td>
          <td>${escapeHtml(track.artist)}</td>
          <td>${escapeHtml(track.album)}</td>
          <td>${track.year || ""}</td>
          <td>${formatTrackDuration(track.duration_seconds)}</td>
          <td>${track.bitrate ? `${track.bitrate} kbps` : ""}</td>
          <td class="actions-cell">
            <button class="action-btn track-delete" title="Delete track from iPod" data-ipod-path="${escapeHtml(track.ipod_path)}" data-title="${escapeHtml(track.title)}">x</button>
          </td>
        </tr>
      `,
    )
    .join("");

  if (!filtered.length) {
    tracksBody.innerHTML = `<tr><td colspan="8">No tracks match the current filter.</td></tr>`;
  }
}

function closeAlbumModal() {
  albumModal.classList.add("hidden");
  albumModalTracks.innerHTML = "";
}

function openAlbumModal(album) {
  const albumTracks = tracks
    .filter((track) => normalizeAlbumKey(track.album) === album.key)
    .sort((a, b) => String(a.title || "").localeCompare(String(b.title || "")));

  const totalDuration = albumTracks.reduce((sum, track) => sum + (track.duration_seconds || 0), 0);
  albumModalCover.src = coverUrl(album.representative);
  albumModalCover.onerror = () => {
    albumModalCover.src = COVER_PLACEHOLDER;
  };
  albumModalTitle.textContent = album.album;
  albumModalMeta.textContent = `${album.artists.join(", ")} • ${album.track_count} track(s) • ${formatDuration(totalDuration)}`;
  albumModalTracks.innerHTML = albumTracks
    .map(
      (track) => `
        <tr>
          <td>${escapeHtml(track.title)}</td>
          <td>${escapeHtml(track.artist)}</td>
          <td>${track.year || ""}</td>
          <td>${formatTrackDuration(track.duration_seconds)}</td>
          <td>${track.bitrate ? `${track.bitrate} kbps` : ""}</td>
        </tr>
      `,
    )
    .join("");

  albumModal.classList.remove("hidden");
}

async function parseApiResponse(response, fallbackMessage) {
  const contentType = String(response.headers.get("content-type") || "").toLowerCase();
  if (contentType.includes("application/json")) {
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || fallbackMessage);
    }
    return data;
  }

  const text = (await response.text()).trim();
  if (!response.ok) {
    throw new Error(text || fallbackMessage);
  }
  throw new Error(text || fallbackMessage);
}

async function loadLibrary(mountpoint, showLoadingStatus = true) {
  if (showLoadingStatus) {
    setStatus(`Loading library from ${mountpoint} ...`);
  }
  loadButton.disabled = true;
  try {
    const response = await fetch(`/api/library?mountpoint=${encodeURIComponent(mountpoint)}`);
    const data = await parseApiResponse(response, "Failed to load iPod library.");

    currentMountpoint = data.mountpoint;
    tracks = data.tracks || [];
    closeAlbumModal();
    buildAlbums();
    updateStats(data);
    renderAlbumStrip();
    applyFilters();
    updateWriteUi();

    statsEl.classList.remove("hidden");
    browserEl.classList.remove("hidden");
    if (showLoadingStatus) {
      setStatus(`Loaded ${data.track_count.toLocaleString()} tracks from ${data.mountpoint}.`);
    }
  } catch (error) {
    currentMountpoint = "";
    tracks = [];
    albums = [];
    closeAlbumModal();
    albumStripEl.innerHTML = "";
    updateWriteUi();
    statsEl.classList.add("hidden");
    browserEl.classList.add("hidden");
    setStatus(error.message, true);
  } finally {
    loadButton.disabled = false;
  }
}

async function deleteByPaths(ipodPaths, descriptor) {
  if (actionInFlight || uploadInFlight) {
    return;
  }
  if (!currentMountpoint) {
    setStatus("Load a library first.", true);
    return;
  }

  actionInFlight = true;
  setStatus(`Deleting ${descriptor} ...`);
  try {
    const response = await fetch("/api/delete-tracks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mountpoint: currentMountpoint,
        ipod_paths: ipodPaths,
      }),
    });
    const data = await parseApiResponse(response, "Failed to delete from iPod.");

    await loadLibrary(currentMountpoint, false);
    setStatus(data.message);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    actionInFlight = false;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const mountpoint = mountpointInput.value.trim();
  if (!mountpoint) {
    setStatus("Mountpoint is required.", true);
    return;
  }
  await loadLibrary(mountpoint);
});

addForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (uploadInFlight || actionInFlight) {
    return;
  }
  if (!currentMountpoint) {
    setStatus("Load your iPod library first before adding files.", true);
    return;
  }

  const files = collectSelectedFiles();
  if (!files.length) {
    setStatus("Select one or more files or a folder to add.", true);
    return;
  }

  const formData = new FormData();
  formData.append("mountpoint", currentMountpoint);
  for (const file of files) {
    formData.append("files", file);
  }

  uploadInFlight = true;
  updateWriteUi();
  setStatus(`Adding ${files.length} file(s) to iPod ...`);
  try {
    const response = await fetch("/api/add-tracks", {
      method: "POST",
      body: formData,
    });
    const data = await parseApiResponse(response, "Failed to add tracks.");

    clearSelectedFiles();
    await loadLibrary(currentMountpoint, false);
    setStatus(data.message || `Added ${files.length} track(s).`);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    uploadInFlight = false;
    updateWriteUi();
  }
});

searchEl.addEventListener("input", applyFilters);
sortEl.addEventListener("change", applyFilters);

tracksBody.addEventListener("click", async (event) => {
  const button = event.target.closest(".track-delete");
  if (!button) {
    return;
  }

  const ipodPath = button.dataset.ipodPath;
  const title = button.dataset.title || "this track";
  if (!ipodPath) {
    return;
  }

  const confirmed = window.confirm(`Delete "${title}" from your iPod? This cannot be undone.`);
  if (!confirmed) {
    return;
  }
  await deleteByPaths([ipodPath], `track "${title}"`);
});

albumStripEl.addEventListener("click", async (event) => {
  const deleteButton = event.target.closest(".album-delete");
  if (deleteButton) {
    const key = deleteButton.dataset.albumKey;
    const album = albums.find((item) => item.key === key);
    if (!album) {
      return;
    }

    const confirmed = window.confirm(
      `Delete album "${album.album}" (${album.track_count} track(s)) from your iPod? This cannot be undone.`,
    );
    if (!confirmed) {
      return;
    }
    await deleteByPaths(album.ipod_paths, `album "${album.album}"`);
    return;
  }

  const card = event.target.closest(".album-card");
  if (!card) {
    return;
  }
  const key = card.dataset.albumKey;
  const album = albums.find((item) => item.key === key);
  if (!album) {
    return;
  }
  openAlbumModal(album);
});

albumModalClose.addEventListener("click", () => {
  closeAlbumModal();
});

albumModal.addEventListener("click", (event) => {
  if (event.target === albumModal) {
    closeAlbumModal();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !albumModal.classList.contains("hidden")) {
    closeAlbumModal();
  }
});

selectMusicButton.addEventListener("click", () => {
  if (uploadInFlight) {
    return;
  }
  if (musicSourceMode.value === "folder") {
    musicFolderInput.click();
  } else {
    musicFilesInput.click();
  }
});

clearSelectionButton.addEventListener("click", () => {
  if (uploadInFlight) {
    return;
  }
  clearSelectedFiles();
});

musicFilesInput.addEventListener("change", updateSelectionSummary);
musicFolderInput.addEventListener("change", updateSelectionSummary);
musicSourceMode.addEventListener("change", () => {
  clearSelectedFiles();
});
updateSelectionSummary();
