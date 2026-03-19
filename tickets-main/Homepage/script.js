const pageMap = {
  home: 'home-page',
  about: 'about-page',
  tickets: 'tickets-page',
  live: 'live-page',
  'moments-2025': 'moments-2025-page',
  'moments-2026': 'moments-2026-page'
};

const navLinks = document.querySelectorAll('.nav-link');
const pages = document.querySelectorAll('.page');

function setActivePage(key) {
  const pageId = pageMap[key] || 'home-page';
  const target = document.getElementById(pageId);
  if (!target) return;

  pages.forEach((page) => page.classList.remove('active'));
  target.classList.add('active');

  navLinks.forEach((link) => {
    const linkKey = link.getAttribute('data-page');
    if (linkKey === key) link.classList.add('active');
    else link.classList.remove('active');
  });

  if (history.replaceState) {
    history.replaceState(null, '', '#' + key);
  } else {
    location.hash = key;
  }

  window.scrollTo({ top: 0, behavior: 'smooth' });
}

navLinks.forEach((link) => {
  link.addEventListener('click', (event) => {
    event.preventDefault();
    const page = link.getAttribute('data-page');
    setActivePage(page);
  });
});

const initialHash = window.location.hash.replace('#', '');
if (initialHash && Object.prototype.hasOwnProperty.call(pageMap, initialHash)) {
  setActivePage(initialHash);
} else {
  setActivePage('home');
}

// Load uploaded photos for 2026 Moments gallery
function loadUploadedPhotos() {
  const gallery = document.getElementById('moments2026Gallery');
  if (!gallery) return;

  // List of uploaded photos to display
  // Photos should be placed in images/uploads/ folder
  const uploadedPhotos = [
    'images/uploads/photo1.jpg',
    'images/uploads/photo2.jpg',
    'images/uploads/photo3.jpg',
    'images/uploads/photo4.jpg',
    'images/uploads/photo5.jpg',
    'images/uploads/photo6.jpg',
  ];

  // Try to load uploaded photos
  let photosFound = 0;
  
  uploadedPhotos.forEach((photoPath, index) => {
    const img = new Image();
    img.onload = function() {
      photosFound++;
      const item = document.createElement('div');
      item.className = 'gallery-item';
      item.onclick = function() { openLightbox(this); };
      item.innerHTML = `<img src="${photoPath}" alt="2026 Event Photo ${index + 1}">`;
      gallery.appendChild(item);
    };
    img.src = photoPath;
  });

  // If no photos found, show placeholder message
  setTimeout(() => {
    if (photosFound === 0 && gallery.children.length === 0) {
      gallery.innerHTML = `
        <div style="grid-column: 1/-1; text-align: center; padding: 40px; color: rgba(233, 213, 255, 0.6);">
          <p style="margin-bottom: 15px;">📸 No photos uploaded yet</p>
          <p style="font-size: 14px;">Go to <a href="admin.html" style="color: #60A5FA; text-decoration: none;">Admin Panel</a> to upload photos from the 2026 event</p>
        </div>
      `;
    }
  }, 1000);
}

// Load photos when page loads or when 2026 moments page is viewed
document.addEventListener('DOMContentLoaded', loadUploadedPhotos);
setActivePage = (function(originalSetActivePage) {
  return function(key) {
    originalSetActivePage.call(this, key);
    if (key === 'moments-2026') {
      // Reload photos when viewing 2026 moments page
      setTimeout(loadUploadedPhotos, 100);
    }
  };
})(setActivePage);

// Lightbox functionality
function openLightbox(element) {
  const lightbox = document.getElementById('lightbox');
  const lightboxImg = document.getElementById('lightboxImg');
  const img = element.querySelector('img');
  
  if (img) {
    lightboxImg.src = img.src;
    lightbox.style.display = 'flex';
  }
}

function closeLightbox() {
  const lightbox = document.getElementById('lightbox');
  lightbox.style.display = 'none';
}
