"""
Bibliothek zur Bewegungserkennung zwischen zwei Bildern
"""

import cv2
import numpy as np
from typing import List, Tuple, Optional
from dataclasses import dataclass


@dataclass
class MotionPixel:
    """Repräsentiert einen Pixel mit erkannter Bewegung"""
    x: int
    y: int
    center_x: int
    center_y: int
    width: int
    height: int
    area: float
    
    def __str__(self):
        return f"({self.center_x}, {self.center_y})"
    
    def __repr__(self):
        return self.__str__()


class MotionDetector:
    """
    Klasse zur Erkennung von Bewegungen zwischen zwei Screenshots
    
    Beispiel:
        detector = MotionDetector(threshold=7, min_area=0)
        motion_pixels = detector.detect_motion(frame1, frame2)
        
        # Mit dynamischem Threshold
        detector = MotionDetector(
            threshold_mode='adaptive',
            adaptive_percentile=95
        )
        
        # Mit Debug-Bild
        motion_pixels = detector.detect_motion(
            frame1, frame2, 
            save_debug_image=True, 
            debug_path="debug.png"
        )
    """
    
    def __init__(
        self, 
        threshold: int = 7, 
        min_area: int = 0,
        blur_kernel: Tuple[int, int] = (5, 5),
        threshold_mode: str = 'fixed',
        adaptive_percentile: float = 95.0,
        otsu_multiplier: float = 1.0,
        local_blocksize: int = 51,
        local_c: int = 2
    ):
        """
        Initialisiert den MotionDetector
        
        Args:
            threshold: Schwellwert für Bewegungserkennung (0-255) bei 'fixed' Mode
            min_area: Minimale Konturfläche zur Rauschunterdrückung
            blur_kernel: Kernel-Größe für Gaussian Blur (muss ungerade sein)
            threshold_mode: Art der Schwellwertbestimmung:
                - 'fixed': Fester Schwellwert (Standard)
                - 'adaptive': Basiert auf Percentil der Differenzwerte
                - 'otsu': Automatische Schwellwertbestimmung nach Otsu
                - 'local': Adaptiver lokaler Threshold
            adaptive_percentile: Percentil für 'adaptive' Mode (z.B. 95 = nur obere 5%)
            otsu_multiplier: Multiplikator für Otsu-Threshold (z.B. 0.8 = 80% des Otsu-Werts)
            local_blocksize: Blockgröße für lokalen Threshold (muss ungerade sein)
            local_c: Konstante, die vom Mittelwert abgezogen wird (lokaler Threshold)
        """
        self.threshold = threshold
        self.min_area = min_area
        self.blur_kernel = blur_kernel
        self.threshold_mode = threshold_mode
        self.adaptive_percentile = adaptive_percentile
        self.otsu_multiplier = otsu_multiplier
        self.local_blocksize = local_blocksize
        self.local_c = local_c
    
    def detect_motion(
        self,
        frame1: np.ndarray,
        frame2: np.ndarray,
        save_debug_image: bool = False,
        debug_path: Optional[str] = None
    ) -> List[MotionPixel]:
        """
        Erkennt Bewegungen zwischen zwei Frames
        
        Args:
            frame1: Erster Screenshot (BGR oder Graustufen)
            frame2: Zweiter Screenshot (BGR oder Graustufen)
            save_debug_image: Wenn True, wird Differenzbild gespeichert
            debug_path: Pfad für Debug-Bild (Standard: "debug_diff.png")
        
        Returns:
            Liste von MotionPixel-Objekten mit erkannten Bewegungen
        
        Raises:
            ValueError: Wenn Frames unterschiedliche Dimensionen haben
        """
        # Dimensionen prüfen
        if frame1.shape[:2] != frame2.shape[:2]:
            raise ValueError(
                f"Frames müssen gleiche Dimensionen haben. "
                f"Frame1: {frame1.shape[:2]}, Frame2: {frame2.shape[:2]}"
            )
        
        # In Graustufen konvertieren, falls nötig
        gray1 = self._to_grayscale(frame1)
        gray2 = self._to_grayscale(frame2)
        
        # Differenzbild berechnen (ohne absdiff, um Ghostpixel zu vermeiden)
        # Subtrahiere frame2 von frame1, um Übergänge von hell zu dunkel zu erkennen
        # Negative Werte werden auf 0 gesetzt
        diff = np.subtract(gray1.astype(np.int16), gray2.astype(np.int16))
        diff = np.clip(diff, 0, 255).astype(np.uint8)
        diff = cv2.GaussianBlur(diff, self.blur_kernel, 0)
        
        # Threshold anwenden (je nach Modus)
        thresh = self._apply_threshold(diff)
        
        # Morphologische Operationen für bessere Vogelerkennung
        # Schließt kleine Lücken und entfernt Rauschen
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
        
        # Konturen finden
        contours, _ = cv2.findContours(
            thresh, 
            cv2.RETR_EXTERNAL, 
            cv2.CHAIN_APPROX_SIMPLE
        )
        
        # Motion Pixels extrahieren
        motion_pixels = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area >= self.min_area:
                x, y, w, h = cv2.boundingRect(contour)
                motion_pixels.append(MotionPixel(
                    x=x,
                    y=y,
                    center_x=x + w // 2,
                    center_y=y + h // 2,
                    width=w,
                    height=h,
                    area=area
                ))
        
        # Optional: Debug-Bild speichern
        if save_debug_image:
            self._save_debug_image(diff, motion_pixels, debug_path)
        
        return motion_pixels
    
    def _to_grayscale(self, frame: np.ndarray) -> np.ndarray:
        """Konvertiert Frame zu Graustufen, falls nötig"""
        if len(frame.shape) == 3:
            return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return frame
    
    def _apply_threshold(self, diff: np.ndarray) -> np.ndarray:
        """
        Wendet Threshold basierend auf dem gewählten Modus an
        
        Args:
            diff: Differenzbild
            
        Returns:
            Binäres Schwellwertbild
        """
        if self.threshold_mode == 'fixed':
            # Fester Schwellwert
            _, thresh = cv2.threshold(diff, self.threshold, 255, cv2.THRESH_BINARY)
            
        elif self.threshold_mode == 'adaptive':
            # Dynamischer Threshold basierend auf Percentil
            # Ignoriert Hintergrund (0-Werte) und nutzt nur tatsächliche Differenzen
            non_zero = diff[diff > 0]
            if len(non_zero) == 0:
                # Keine Bewegung -> leeres Bild zurück
                thresh = np.zeros_like(diff)
            else:
                dynamic_threshold = np.percentile(non_zero, self.adaptive_percentile)
                _, thresh = cv2.threshold(diff, dynamic_threshold, 255, cv2.THRESH_BINARY)
                
        elif self.threshold_mode == 'otsu':
            # Otsu's Methode für automatische Schwellwertbestimmung
            otsu_thresh, thresh = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            # Optional: Otsu-Wert mit Multiplikator anpassen
            if self.otsu_multiplier != 1.0:
                adjusted_thresh = otsu_thresh * self.otsu_multiplier
                _, thresh = cv2.threshold(diff, adjusted_thresh, 255, cv2.THRESH_BINARY)
                
        elif self.threshold_mode == 'local':
            # Lokaler adaptiver Threshold (gut für unterschiedliche Beleuchtung)
            thresh = cv2.adaptiveThreshold(
                diff, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                cv2.THRESH_BINARY, self.local_blocksize, -self.local_c
            )
            
        else:
            raise ValueError(f"Unbekannter threshold_mode: {self.threshold_mode}")
        
        return thresh
    
    def get_threshold_info(self, diff: np.ndarray) -> dict:
        """
        Gibt Informationen über die berechneten Schwellwerte zurück (für Debugging)
        
        Args:
            diff: Differenzbild
            
        Returns:
            Dictionary mit Threshold-Informationen
        """
        info = {
            'mode': self.threshold_mode,
            'max_diff': np.max(diff),
            'mean_diff': np.mean(diff[diff > 0]) if np.any(diff > 0) else 0,
            'median_diff': np.median(diff[diff > 0]) if np.any(diff > 0) else 0,
            'non_zero_pixels': np.count_nonzero(diff)
        }
        
        if self.threshold_mode == 'fixed':
            info['threshold_used'] = self.threshold
        elif self.threshold_mode == 'adaptive':
            non_zero = diff[diff > 0]
            if len(non_zero) > 0:
                info['threshold_used'] = np.percentile(non_zero, self.adaptive_percentile)
            else:
                info['threshold_used'] = 0
        elif self.threshold_mode == 'otsu':
            otsu_thresh, _ = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            info['otsu_threshold'] = otsu_thresh
            info['threshold_used'] = otsu_thresh * self.otsu_multiplier
            
        return info
    
    def _save_debug_image(
        self, 
        diff: np.ndarray, 
        motion_pixels: List[MotionPixel],
        debug_path: Optional[str] = None
    ) -> None:
        """Speichert Differenzbild mit markierten Bewegungen"""
        if debug_path is None:
            debug_path = "debug_diff.png"
        
        # Differenzbild normalisieren und zu BGR konvertieren
        diff_norm = cv2.normalize(diff, None, 0, 255, cv2.NORM_MINMAX)
        vis = cv2.cvtColor(diff_norm.astype(np.uint8), cv2.COLOR_GRAY2BGR)
        
        # Bewegungen markieren
        for mp in motion_pixels:
            # Fadenkreuz im Zentrum
            cv2.drawMarker(
                vis, 
                (mp.center_x, mp.center_y), 
                (0, 0, 255),
                markerType=cv2.MARKER_CROSS, 
                markerSize=15, 
                thickness=1
            )
            # Bounding Box
            cv2.rectangle(
                vis, 
                (mp.x, mp.y), 
                (mp.x + mp.width, mp.y + mp.height), 
                (0, 255, 0), 
                1
            )
        
        cv2.imwrite(debug_path, vis)


# Convenience-Funktion für einfache Verwendung
def detect_motion_simple(
    frame1: np.ndarray,
    frame2: np.ndarray,
    threshold: int = 7,
    min_area: int = 0,
    save_debug_image: bool = False,
    debug_path: Optional[str] = None,
    threshold_mode: str = 'fixed',
    adaptive_percentile: float = 95.0
) -> List[MotionPixel]:
    """
    Einfache Funktion zur Bewegungserkennung ohne Klasseninstanziierung
    
    Args:
        frame1: Erster Screenshot
        frame2: Zweiter Screenshot
        threshold: Schwellwert für Bewegungserkennung (bei 'fixed' mode)
        min_area: Minimale Konturfläche
        save_debug_image: Debug-Bild speichern
        debug_path: Pfad für Debug-Bild
        threshold_mode: 'fixed', 'adaptive', 'otsu', oder 'local'
        adaptive_percentile: Percentil für adaptive mode (z.B. 95)
    
    Returns:
        Liste von MotionPixel-Objekten
    """
    detector = MotionDetector(
        threshold=threshold, 
        min_area=min_area,
        threshold_mode=threshold_mode,
        adaptive_percentile=adaptive_percentile
    )
    return detector.detect_motion(frame1, frame2, save_debug_image, debug_path)