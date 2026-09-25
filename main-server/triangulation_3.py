"""
#############################################################
VP - Umweltrover - Triangulation Library

Basierend auf der Originalversion von:
Moritz C. Broekling
Athanasios Georgiadis

Angepasst und weiterentwickelt von:
Leon Key

Beschreibung:
Diese Datei verwendet den urspruenglichen Code von
Moritz C. Broekling und Athanasios Georgiadis als
Grundlage. Fuer das vorliegende Projekt wurde der Code
ueberarbeitet, angepasst und funktional erweitert.
Die Originalautoren bleiben von diesen Anpassungen
unberuehrt; aenderungen betreffen ausschliesslich die
hier vorliegende abgeleitete Version.

# Hinweise zu den Anpassungen:
# Es wurde keine neue Triangulationslogik entwickelt, sondern bestehende Fehler
# in der Auswertung und Darstellung der Kamerageometrie wurden korrigiert.
#
# Erkenntnisse aus dem Feldtest:
# - Die GPS-basierte Anordnung der Kameras war grundsaetzlich korrekt.
# - Der ENU-Ursprung musste auf eine feste Referenzkamera gelegt werden,
#   damit pi1 stabil bei 0/0/0 liegt und der Positionsbezug nicht von der
#   Ankunftsreihenfolge der Daten abhaengt.
# - Der Event-Zeitstempel musste aus dem Header der Referenzkamera uebernommen werden,
#   damit nicht still auf die Systemzeit zurueckgefallen wird.
# - Fuer die Richtungsvektoren der Kameras muss der kalibrierte Hauptpunkt
#   verwendet werden, nicht ein fest angenommenes Bildzentrum.
# - Bildpunkte werden vor der Strahlberechnung entzerrt, damit Kalibrierung
#   und Richtungsvektor konsistent zusammenarbeiten.
# - Das Yaw-Vorzeichen des BNO musste invertiert werden, damit die im Plot
#   dargestellte Blickrichtung der realen Ausrichtung im Feld entspricht.
#
# Ziel der aenderungen:
# Bestehende Logik beibehalten, aber erkannte systematische Fehler in
# Referenzbezug, Zeitstempel und Orientierungsdarstellung gezielt beheben.
#zeichen ae ue oe ss angepasst
#############################################################
"""

import numpy as np
import plotly.graph_objects as go
import yaml
import json
import cv2
from dataclasses import dataclass
from typing import List, Tuple, Optional, Union
from motion_detector import MotionPixel

# WGS84-Ellipsoid-Konstanten
a = 6378137.0            # aequatorradius [m]
f = 1 / 298.257223563    # Abplattung
e_sq = f * (2 - f)


@dataclass
class TriangulationResult:
    """Ergebnis einer Triangulation"""
    point_3d: np.ndarray  # ENU-Koordinaten (E, N, U)
    mean_distance: float  # Mittlerer Abstand der Sichtlinien in Metern
    gps_coords: Tuple[float, float, float]  # GPS-Koordinaten (lat, lon, h)
    motion_pixels: List[List[MotionPixel]]  # Verwendete MotionPixels pro Kamera
    cameras_used: List['Kamera']  # Verwendete Kameras
    
    def __str__(self):
        return (f"TriangulationResult(ENU={self.point_3d}, "
                f"GPS=({self.gps_coords[0]:.6f}°, {self.gps_coords[1]:.6f}°, {self.gps_coords[2]:.1f}m), "
                f"Δ={self.mean_distance:.3f}m)")
    
    def __repr__(self):
        return self.__str__()


def geodetic_to_enu(lat, lon, h, lat_ref, lon_ref, h_ref):
    """
    Direkte Umrechnung von geodaetischen Koordinaten (lat, lon, h)
    in lokale ENU-Koordinaten (East, North, Up) relativ zum Referenzpunkt.
    """
    lat_rad = np.radians(lat)
    lon_rad = np.radians(lon)
    lat_ref_rad = np.radians(lat_ref)
    lon_ref_rad = np.radians(lon_ref)

    sin_lat, cos_lat = np.sin(lat_rad), np.cos(lat_rad)
    sin_lon, cos_lon = np.sin(lon_rad), np.cos(lon_rad)
    sin_lat0, cos_lat0 = np.sin(lat_ref_rad), np.cos(lat_ref_rad)
    sin_lon0, cos_lon0 = np.sin(lon_ref_rad), np.cos(lon_ref_rad)

    N = a / np.sqrt(1 - e_sq * sin_lat**2)
    N0 = a / np.sqrt(1 - e_sq * sin_lat0**2)

    factor = N + h
    x = factor * cos_lat * cos_lon
    y = factor * cos_lat * sin_lon
    z = (N * (1 - e_sq) + h) * sin_lat

    factor0 = N0 + h_ref
    x0 = factor0 * cos_lat0 * cos_lon0
    y0 = factor0 * cos_lat0 * sin_lon0
    z0 = (N0 * (1 - e_sq) + h_ref) * sin_lat0

    delta = np.array([x - x0, y - y0, z - z0])

    rot_matrix = np.array([
        [-sin_lon0, cos_lon0, 0],
        [-sin_lat0 * cos_lon0, -sin_lat0 * sin_lon0, cos_lat0],
        [cos_lat0 * cos_lon0, cos_lat0 * sin_lon0, sin_lat0]
    ])
    
    return rot_matrix @ delta


def enu_to_geodetic(e, n, u, lat_ref, lon_ref, h_ref):
    """
    Konvertiert ENU-Koordinaten zurueck zu geodaetischen Koordinaten (lat, lon, h).
    """
    lat_ref_rad = np.radians(lat_ref)
    lon_ref_rad = np.radians(lon_ref)
    
    sin_lat0, cos_lat0 = np.sin(lat_ref_rad), np.cos(lat_ref_rad)
    sin_lon0, cos_lon0 = np.sin(lon_ref_rad), np.cos(lon_ref_rad)
    
    # Inverse Rotationsmatrix
    rot_matrix_inv = np.array([
        [-sin_lon0, -sin_lat0 * cos_lon0, cos_lat0 * cos_lon0],
        [cos_lon0, -sin_lat0 * sin_lon0, cos_lat0 * sin_lon0],
        [0, cos_lat0, sin_lat0]
    ])
    
    # ENU zu ECEF-Delta
    delta = rot_matrix_inv @ np.array([e, n, u])
    
    # Referenzpunkt in ECEF
    N0 = a / np.sqrt(1 - e_sq * sin_lat0**2)
    factor0 = N0 + h_ref
    x0 = factor0 * cos_lat0 * cos_lon0
    y0 = factor0 * cos_lat0 * sin_lon0
    z0 = (N0 * (1 - e_sq) + h_ref) * sin_lat0
    
    # ECEF-Koordinaten des Punktes
    x = x0 + delta[0]
    y = y0 + delta[1]
    z = z0 + delta[2]
    
    # ECEF zu geodaetisch (iterative Loesung)
    lon = np.arctan2(y, x)
    p = np.sqrt(x**2 + y**2)
    lat = np.arctan2(z, p * (1 - e_sq))
    
    # Iteration fuer Praezision
    for _ in range(5):
        N = a / np.sqrt(1 - e_sq * np.sin(lat)**2)
        h = p / np.cos(lat) - N
        lat = np.arctan2(z, p * (1 - e_sq * N / (N + h)))
    
    N = a / np.sqrt(1 - e_sq * np.sin(lat)**2)
    h = p / np.cos(lat) - N
    
    return np.degrees(lat), np.degrees(lon), h


class Kamera:
    """
    Repraesentiert eine Kamera mit Lage, Orientierung und Kalibrierungsparametern.
    Kann MotionPixel-Objekte fuer die Triangulation verwenden.
    """

    def __init__(self,
                 motion_pixels: Union[MotionPixel, List[MotionPixel], Tuple[float, float], List[Tuple[float, float]]] = None,
                 gps_koordinate: Tuple[float, float, float] = None,
                 gps_ref: Tuple[float, float, float] = None,
                 orientierung: Tuple[float, float, float] = None,
                 intrinsisch: np.ndarray = None,
                 distortion: np.ndarray = None):
        """
        Parameter:
        ----------
        motion_pixels : MotionPixel | list[MotionPixel] | (u, v) | list[(u, v)], optional
            Bewegungspixel oder Bildpunkte fuer die Triangulation.
            Kann sein:
            - Ein einzelner MotionPixel
            - Eine Liste von MotionPixels
            - Ein Tupel (u, v) als Bildpunkt
            - Eine Liste von Tupeln fuer mehrere Bildpunkte
        gps_koordinate : (lat, lon, h)
            GPS-Koordinaten in Grad und Meter.
        gps_ref : (lat_ref, lon_ref, h_ref)
            Referenzpunkt fuer ENU-Koordinaten.
        orientierung : (yaw, pitch, roll)
            Orientierung in Grad.
        intrinsisch : 3×3 np.ndarray
            Intrinsische Kameramatrix.
        distortion : np.ndarray
            Verzerrungskoeffizienten.
        """
        self.motion_pixels = []
        self.bildpunkte = []
        
        # Motion Pixels verarbeiten
        if motion_pixels is not None:
            if isinstance(motion_pixels, MotionPixel):
                self.motion_pixels = [motion_pixels]
                self.bildpunkte = [np.array([motion_pixels.center_x, motion_pixels.center_y], dtype=float)]
            elif isinstance(motion_pixels, list):
                if len(motion_pixels) > 0:
                    if isinstance(motion_pixels[0], MotionPixel):
                        self.motion_pixels = motion_pixels
                        self.bildpunkte = [np.array([mp.center_x, mp.center_y], dtype=float) 
                                          for mp in motion_pixels]
                    elif isinstance(motion_pixels[0], (tuple, list)):
                        # Liste von Tupeln (u, v)
                        self.bildpunkte = [np.array(p, dtype=float) for p in motion_pixels]
            elif isinstance(motion_pixels, (tuple, list)) and len(motion_pixels) == 2:
                # Einzelner Punkt als Tupel
                self.bildpunkte = [np.array(motion_pixels, dtype=float)]
        
        self.gps_koordinate = gps_koordinate
        self.gps_ref = gps_ref
        
        if gps_koordinate and gps_ref:
            self.enu_koordinate = geodetic_to_enu(
                gps_koordinate[0], gps_koordinate[1], gps_koordinate[2], 
                gps_ref[0], gps_ref[1], gps_ref[2]
            )
        else:
            self.enu_koordinate = None
        
        if orientierung:
            # Erwartetes Verhalten im ENU-System:
            # - yaw:   0° = Norden, positive Werte im Uhrzeigersinn (Kompass)
            # - pitch: positive Werte = Kamera schaut nach oben
            # - roll:  Drehung um die optische Achse
            #
            # WICHTIG:
            # Der bisherige feste +180°-Roll-Offset hat die Sichtstrahlen gespiegelt
            # und den Himmel in den Boden geklappt. Dieser Blind-Offset wird hier
            # bewusst entfernt.
            self.yaw, self.pitch, self.roll = orientierung
        else:
            self.yaw = self.pitch = self.roll = 0
        
        self.K = np.asarray(intrinsisch, dtype=float) if intrinsisch is not None else None
        self.distortion = np.asarray(distortion, dtype=float) if distortion is not None else None
        
        # Rotationsmatrix cachen
        self._R = self._compute_rotation_matrix() if orientierung else None

    @classmethod
    def from_json(cls,
                  json_pfad: str,
                  yaml_pfad: str,
                  motion_pixels: Union[MotionPixel, List[MotionPixel], Tuple[float, float], List[Tuple[float, float]]],
                  gps_ref: Tuple[float, float, float]):
        """
        Erstellt Kamera-Objekt aus JSON- und YAML-Dateien mit MotionPixels.

        Parameter:
        ----------
        json_pfad : str
            Pfad zur JSON-Datei mit GPS und Orientierung.
        yaml_pfad : str
            Pfad zur YAML-Datei mit Kalibrierungsparametern.
        motion_pixels : MotionPixel | list[MotionPixel] | (u, v) | list[(u, v)]
            Bewegungspixel oder Bildpunkte.
        gps_ref : (lat_ref, lon_ref, h_ref)
            Referenzpunkt fuer ENU-Koordinaten.
        """
        with open(json_pfad, 'r') as f:
            json_data = json.load(f)

        with open(yaml_pfad, 'r') as f:
            calib_data = yaml.safe_load(f)

        gps_koordinate = (
            json_data['gps']['lat'],
            json_data['gps']['lon'],
            json_data['gps']['alt']
        )

        orientierung = (
            json_data['orientation']['yaw'],
            json_data['orientation']['pitch'],
            json_data['orientation']['roll']
        )

        intrinsisch = np.array(calib_data['camera_matrix'])
        distortion = np.array(calib_data['dist_coefs']).flatten()

        return cls(
            motion_pixels=motion_pixels,
            gps_koordinate=gps_koordinate,
            gps_ref=gps_ref,
            orientierung=orientierung,
            intrinsisch=intrinsisch,
            distortion=distortion
        )

    @classmethod
    def from_header(cls,
                    header: dict,
                    yaml_pfad: str,
                    motion_pixels: Union[MotionPixel, List[MotionPixel], Tuple[float, float], List[Tuple[float, float]]],
                    gps_ref: Tuple[float, float, float]):
        """
        Erstellt Kamera-Objekt aus HTTP-Header und YAML-Datei mit MotionPixels.

        Parameter:
        ----------
        header : dict
            HTTP-Header-Dictionary mit GPS und Orientierung.
        yaml_pfad : str
            Pfad zur YAML-Datei mit Kalibrierungsparametern.
        motion_pixels : MotionPixel | list[MotionPixel] | (u, v) | list[(u, v)]
            Bewegungspixel oder Bildpunkte.
        gps_ref : (lat_ref, lon_ref, h_ref)
            Referenzpunkt fuer ENU-Koordinaten.
        """
        with open(yaml_pfad, 'r') as f:
            calib_data = yaml.safe_load(f)

        gps_koordinate = (
            header['gps']['lat'],
            header['gps']['lon'],
            header['gps']['alt']
        )

        orientierung = (
            header['orientation']['yaw'],
            header['orientation']['pitch'],
            header['orientation']['roll']
        )

        intrinsisch = np.array(calib_data['camera_matrix'])
        distortion = np.array(calib_data['dist_coefs']).flatten()

        return cls(
            motion_pixels=motion_pixels,
            gps_koordinate=gps_koordinate,
            gps_ref=gps_ref,
            orientierung=orientierung,
            intrinsisch=intrinsisch,
            distortion=distortion
        )

    def _compute_rotation_matrix(self) -> np.ndarray:
        """
        Rotationslogik passend zur Ausrichtungsprozedur:

        - Null-Lage nach "Set Zero":
        Kamera blickt nach Norden, Kamera steht horizontal.
        - yaw  = Heading relativ zu Nord
        - roll = Neigung der Kamera nach oben/unten
        - pitch = seitliche Kippung / Bildverdrehung um die optische Achse
        """
        # BNO-Yaw ist relativ zur ENU-Plotkonvention invertiert.
        # Deshalb wird das Vorzeichen hier umgedreht, damit die Kamerarichtung
        # im Plot der realen Ausrichtung im Feld entspricht.
        y = np.radians(-self.yaw)
        e = np.radians(self.roll)
        t = np.radians(self.pitch)

        cy, sy = np.cos(y), np.sin(y)
        ce, se = np.cos(e), np.sin(e)
        ct, st = np.cos(t), np.sin(t)

        # Kamera-Null-Lage -> ENU:
        # x_cam = rechts  -> East
        # y_cam = unten   -> -Up
        # z_cam = vorwaerts -> North
        R0 = np.array([
            [1, 0, 0],
            [0, 0, 1],
            [0, -1, 0]
        ], dtype=float)

        # Heading relativ zu Nord
        R_heading = np.array([
            [cy,  sy, 0],
            [-sy, cy, 0],
            [0,   0, 1]
        ], dtype=float)

        # Kameraneigung nach oben/unten
        R_elev = np.array([
            [1, 0, 0],
            [0, ce, -se],
            [0, se,  ce]
        ], dtype=float)

        # Bildverdrehung / seitliche Kippung um die optische Achse
        # beeinflusst die Mittellinie nicht, aber die Off-Center-Pixel
        R_tilt = np.array([
            [ct, -st, 0],
            [st,  ct, 0],
            [0,   0, 1]
        ], dtype=float)

        return R_heading @ R0 @ R_elev @ R_tilt

    def bildpunkt_zu_richtungsvektor(self, bildpunkt) -> np.ndarray:
        """
        Wandelt einen Bildpunkt (u, v) in einen Richtungsvektor im ENU-System um.
        Nutzt zuerst die Entzerrung aus der Kamerakalibrierung.
        """
        uv = np.asarray(bildpunkt, dtype=np.float32).reshape(1, 1, 2)

        if self.K is None:
            raise ValueError("Kameramatrix fehlt")

        if self.distortion is not None and np.size(self.distortion) > 0:
            undist = cv2.undistortPoints(uv, self.K, self.distortion)
            x = float(undist[0, 0, 0])
            y = float(undist[0, 0, 1])
            richtung_cam = np.array([x, y, 1.0], dtype=float)
        else:
            pixel_h = np.array([float(bildpunkt[0]), float(bildpunkt[1]), 1.0], dtype=float)
            richtung_cam = np.linalg.solve(self.K, pixel_h)

        richtung_cam /= np.linalg.norm(richtung_cam)
        richtung_welt = self._R @ richtung_cam
        return richtung_welt / np.linalg.norm(richtung_welt)

    def zeichne_sichtlinien(self, 
                           fig: go.Figure, 
                           farbe: str, 
                           name: str,
                           img_width: int = 4056, 
                           img_height: int = 3040,
                           laenge: float = 250,
                           num_punkte: int = 10,
                           zeige_ecken: bool = False,
                           zeige_mitte: bool = False,
                           zeige_motion_pixels: bool = True) -> go.Figure:
        """
        Zeichnet Sichtlinien der Kamera in einen Plotly-3D-Graph.
        """
        t = np.linspace(0, laenge, num_punkte)
        
        # Motion Pixel Sichtlinien
        if zeige_motion_pixels and self.bildpunkte:
            for i, bp in enumerate(self.bildpunkte):
                vec = self.bildpunkt_zu_richtungsvektor(bp)
                line = self.enu_koordinate[:, np.newaxis] + vec[:, np.newaxis] * t
                
                legend_name = f'{name} Motion {i+1}' if len(self.bildpunkte) > 1 else f'{name} Motion'
                fig.add_trace(go.Scatter3d(
                    x=line[0], y=line[1], z=line[2],
                    mode='lines',
                    name=legend_name,
                    line=dict(color=farbe, width=5)
                ))
        
        # Bildmitte
        if zeige_mitte:
            img_center = (float(self.K[0, 2]), float(self.K[1, 2]))
            center_vec = self.bildpunkt_zu_richtungsvektor(img_center)
            line_center = self.enu_koordinate[:, np.newaxis] + center_vec[:, np.newaxis] * t
            
            fig.add_trace(go.Scatter3d(
                x=line_center[0], y=line_center[1], z=line_center[2],
                mode='lines',
                name=f'{name} Bildmitte',
                line=dict(color=farbe, width=3, dash='dot')
            ))
        
        # Bildecken
        if zeige_ecken:
            corners = [(0, 0), (img_width, 0), (img_width, img_height), (0, img_height)]
            for corner in corners:
                corner_vec = self.bildpunkt_zu_richtungsvektor(corner)
                line_corner = self.enu_koordinate[:, np.newaxis] + corner_vec[:, np.newaxis] * t
                
                fig.add_trace(go.Scatter3d(
                    x=line_corner[0], y=line_corner[1], z=line_corner[2],
                    mode='lines',
                    line=dict(color=farbe, width=2, dash='dash'),
                    showlegend=False
                ))
        
        return fig

    def zeichne_position(self, fig: go.Figure, farbe: str, name: str) -> go.Figure:
        """Zeichnet die Kameraposition als Marker."""
        fig.add_trace(go.Scatter3d(
            x=[self.enu_koordinate[0]],
            y=[self.enu_koordinate[1]],
            z=[self.enu_koordinate[2]],
            mode='markers',
            marker=dict(size=10, color=farbe),
            name=name
        ))
        return fig


def trianguliere_motion_pixels(kameras: List[Kamera],
                                motion_pixels_per_camera: List[Union[MotionPixel, List[MotionPixel]]] = None,
                                max_abstand: float = 1.0,
                                min_kameras: int = 2) -> List[TriangulationResult]:
    """
    Trianguliert 3D-Punkte aus MotionPixels mehrerer Kameras.
    
    Parameter:
    ----------
    kameras : list[Kamera]
        Liste von Kamera-Objekten.
    motion_pixels_per_camera : list[MotionPixel | list[MotionPixel]], optional
        Liste mit MotionPixels pro Kamera. Jeder Eintrag kann sein:
        - Ein einzelner MotionPixel
        - Eine Liste von MotionPixels
        Falls None, werden die in den Kameras gespeicherten Bildpunkte verwendet.
    max_abstand : float
        Maximaler mittlerer Abstand der Sichtlinien in Metern.
    min_kameras : int
        Mindestanzahl Kameras, die einen Punkt sehen muessen.
        
    Returns:
    --------
    list[TriangulationResult]
        Liste von erfolgreich triangulierten Punkten.
        
    Beispiel:
    --------
    >>> cam1 = Kamera.from_json(..., motion_pixels=[mp1, mp2, mp3], ...)
    >>> cam2 = Kamera.from_json(..., motion_pixels=[mp4, mp5], ...)
    >>> cam3 = Kamera.from_json(..., motion_pixels=[mp6], ...)
    >>> results = trianguliere_motion_pixels([cam1, cam2, cam3], max_abstand=0.5)
    """
    if len(kameras) < min_kameras:
        raise ValueError(f"Mindestens {min_kameras} Kameras erforderlich")
    
    # Motion Pixels extrahieren
    if motion_pixels_per_camera is None:
        motion_pixels_per_camera = []
        for cam in kameras:
            if cam.motion_pixels:
                motion_pixels_per_camera.append(cam.motion_pixels)
            elif cam.bildpunkte:
                # Fallback: Bildpunkte ohne MotionPixel-Objekte
                motion_pixels_per_camera.append(cam.bildpunkte)
            else:
                motion_pixels_per_camera.append([])
    
    # Normalisiere zu Listen von Listen
    normalized_pixels = []
    for pixels in motion_pixels_per_camera:
        if isinstance(pixels, MotionPixel):
            normalized_pixels.append([pixels])
        elif isinstance(pixels, list):
            if len(pixels) > 0 and isinstance(pixels[0], MotionPixel):
                normalized_pixels.append(pixels)
            elif len(pixels) > 0 and isinstance(pixels[0], (tuple, list, np.ndarray)):
                # Bildpunkte ohne MotionPixel-Objekte
                normalized_pixels.append(pixels)
            else:
                normalized_pixels.append([])
        else:
            normalized_pixels.append([])
    
    # Alle Sichtlinien sammeln
    all_origins = []
    all_directions = []
    all_pixels_info = []  # (kamera_index, motion_pixel)
    
    for cam_idx, (cam, pixels) in enumerate(zip(kameras, normalized_pixels)):
        for pixel in pixels:
            if isinstance(pixel, MotionPixel):
                bp = np.array([pixel.center_x, pixel.center_y])
            else:
                bp = np.array(pixel)
            
            all_origins.append(cam.enu_koordinate)
            print(cam.enu_koordinate)
            all_directions.append(cam.bildpunkt_zu_richtungsvektor(bp))
            all_pixels_info.append((cam_idx, pixel))
    
    if len(all_origins) < min_kameras:
        print("zu wenig kameras")
        print(all_origins)
        return []
    
    origins = np.array(all_origins)
    directions = np.array(all_directions)
    
    # Least-Squares Triangulation
    n = len(origins)
    A = np.zeros((3, 3))
    b = np.zeros(3)
    
    for i in range(n):
        d = directions[i]
        p = origins[i]
        proj = np.eye(3) - np.outer(d, d)
        A += proj
        b += proj @ p
    
    try:
        schnittpunkt = np.linalg.solve(A, b)
    except np.linalg.LinAlgError:
        print("Lin Alg Error")
        return []
    
    # Validierung
    abstaende = []
    used_cameras = set()
    
    for i in range(n):
        diff = schnittpunkt - origins[i]
        projektion = np.dot(diff, directions[i])
        
        if projektion < 0:  # Punkt hinter Kamera
            print("Punkt hinter kamera")
            return []
        
        abstand = np.linalg.norm(diff - projektion * directions[i])
        abstaende.append(abstand)
        used_cameras.add(all_pixels_info[i][0])
    
    if len(used_cameras) < min_kameras:
        print("zu wenig benutzte Kameras")
        return []
    
    mittlerer_abstand = np.mean(abstaende)
    
    if mittlerer_abstand > max_abstand:
        print("Abstand zu gross")
        return []
    
    # GPS-Koordinaten berechnen
    gps_ref = kameras[0].gps_ref
    lat, lon, h = enu_to_geodetic(
        schnittpunkt[0], schnittpunkt[1], schnittpunkt[2],
        gps_ref[0], gps_ref[1], gps_ref[2]
    )
    
    # MotionPixels gruppieren nach Kamera
    pixels_by_camera = [[] for _ in kameras]
    for cam_idx, pixel in all_pixels_info:
        pixels_by_camera[cam_idx].append(pixel)
    
    result = TriangulationResult(
        point_3d=schnittpunkt,
        mean_distance=mittlerer_abstand,
        gps_coords=(lat, lon, h),
        motion_pixels=pixels_by_camera,
        cameras_used=[kameras[i] for i in used_cameras]
    )
    
    return [result]


def trianguliere_alle_kombinationen(kameras: List[Kamera],
                                     motion_pixels_per_camera: List[List[MotionPixel]] = None,
                                     max_abstand: float = 1.0,
                                     min_kameras: int = 2,
                                     max_ergebnisse: int = None) -> List[TriangulationResult]:
    """
    Findet automatisch zusammengehoerende MotionPixels ueber mehrere Kameras.
    Probiert alle moeglichen Kombinationen und findet Objekte, die von 
    mindestens min_kameras Kameras gesehen werden.
    
    **Wichtig**: Jeder MotionPixel wird als eigenes Objekt betrachtet!
    
    Algorithmus:
    1. Generiert alle moeglichen Kombinationen von je einem MotionPixel pro Kamera
    2. Versucht Triangulation fuer jede Kombination
    3. Behaelt nur Kombinationen mit Abstand < max_abstand
    4. Sortiert nach Qualitaet (Anzahl Kameras, dann mittlerer Abstand)
    
    Parameter:
    ----------
    kameras : list[Kamera]
        Liste von Kamera-Objekten mit motion_pixels.
    motion_pixels_per_camera : list[list[MotionPixel]], optional
        ueberschreibt die in den Kameras gespeicherten MotionPixels.
    max_abstand : float
        Maximaler mittlerer Abstand der Sichtlinien in Metern.
        Kleinere Werte = strengere Filterung = weniger aber genauere Ergebnisse.
    min_kameras : int
        Mindestanzahl Kameras, die ein Objekt sehen muessen (2-N).
    max_ergebnisse : int, optional
        Maximale Anzahl zurueckzugebender Ergebnisse (beste zuerst).
        
    Returns:
    --------
    list[TriangulationResult]
        Alle gefundenen Objekte, sortiert nach Qualitaet:
        1. Mehr Kameras = besser
        2. Kleinerer mittlerer Abstand = besser
        
    Beispiel:
    --------
    >>> # Kamera 1 sieht 3 Objekte, Kamera 2 sieht 2 Objekte, Kamera 3 sieht 1 Objekt
    >>> cam1.motion_pixels = [mp1, mp2, mp3]
    >>> cam2.motion_pixels = [mp4, mp5]
    >>> cam3.motion_pixels = [mp6]
    >>> # Findet automatisch welche zusammengehoeren:
    >>> results = trianguliere_alle_kombinationen(
    ...     [cam1, cam2, cam3], 
    ...     max_abstand=0.5,
    ...     min_kameras=2
    ... )
    >>> # Koennte z.B. finden: mp1+mp4+mp6 = Objekt A, mp2+mp5 = Objekt B
    """
    from itertools import combinations, product
    
    if len(kameras) < min_kameras:
        raise ValueError(f"Mindestens {min_kameras} Kameras erforderlich")
    
    # Motion Pixels extrahieren
    if motion_pixels_per_camera is None:
        motion_pixels_per_camera = []
        for cam in kameras:
            if cam.motion_pixels:
                motion_pixels_per_camera.append(cam.motion_pixels)
            else:
                motion_pixels_per_camera.append([])
    
    # Kameras ohne MotionPixels herausfiltern
    valid_cameras = []
    valid_pixels = []
    for cam, pixels in zip(kameras, motion_pixels_per_camera):
        if len(pixels) > 0:
            valid_cameras.append(cam)
            valid_pixels.append(pixels)
    
    if len(valid_cameras) < min_kameras:
        return []
    
    all_results = []
    
    # Fuer jede moegliche Anzahl von Kameras (von max bis min)
    for num_cams in range(len(valid_cameras), min_kameras - 1, -1):
        # Alle Kombinationen von num_cams Kameras
        for cam_combo_indices in combinations(range(len(valid_cameras)), num_cams):
            selected_cams = [valid_cameras[i] for i in cam_combo_indices]
            selected_pixels = [valid_pixels[i] for i in cam_combo_indices]
            
            # Alle Kombinationen von je einem MotionPixel pro ausgewaehlter Kamera
            for pixel_combo in product(*selected_pixels):
                # Triangulation versuchen
                result = _trianguliere_pixel_kombination(
                    selected_cams, 
                    pixel_combo, 
                    max_abstand
                )
                
                if result is not None:
                    all_results.append(result)
    
    # Nach Qualitaet sortieren: Erst nach Anzahl Kameras (mehr=besser), dann nach Abstand (kleiner=besser)
    all_results.sort(key=lambda r: (-len(r.cameras_used), r.mean_distance))
    
    # Duplikate entfernen (sehr aehnliche 3D-Punkte)
    filtered_results = _remove_duplicate_results(all_results, distance_threshold=0.1)
    
    if max_ergebnisse is not None:
        filtered_results = filtered_results[:max_ergebnisse]
    
    return filtered_results


def _trianguliere_pixel_kombination(kameras: List[Kamera],
                                     pixel_combo: tuple,
                                     max_abstand: float) -> Optional[TriangulationResult]:
    """
    Hilfsfunktion: Trianguliert eine spezifische Kombination von MotionPixels.
    """
    origins = []
    directions = []
    
    for cam, pixel in zip(kameras, pixel_combo):
        if isinstance(pixel, MotionPixel):
            bp = np.array([pixel.center_x, pixel.center_y])
        else:
            bp = np.array(pixel)
        
        origins.append(cam.enu_koordinate)
        directions.append(cam.bildpunkt_zu_richtungsvektor(bp))
    
    origins = np.array(origins)
    directions = np.array(directions)
    
    # Least-Squares Triangulation
    n = len(origins)
    A = np.zeros((3, 3))
    b = np.zeros(3)
    
    for i in range(n):
        d = directions[i]
        p = origins[i]
        proj = np.eye(3) - np.outer(d, d)
        A += proj
        b += proj @ p
    
    try:
        schnittpunkt = np.linalg.solve(A, b)
    except np.linalg.LinAlgError:
        return None
    
    # Validierung
    abstaende = []
    for i in range(n):
        diff = schnittpunkt - origins[i]
        projektion = np.dot(diff, directions[i])
        
        if projektion < 0:  # Punkt hinter Kamera
            return None
        
        abstand = np.linalg.norm(diff - projektion * directions[i])
        abstaende.append(abstand)
    
    mittlerer_abstand = np.mean(abstaende)
    
    if mittlerer_abstand > max_abstand:
        return None
    
    # GPS-Koordinaten berechnen
    gps_ref = kameras[0].gps_ref
    lat, lon, h = enu_to_geodetic(
        schnittpunkt[0], schnittpunkt[1], schnittpunkt[2],
        gps_ref[0], gps_ref[1], gps_ref[2]
    )
    
    # MotionPixels pro Kamera organisieren
    pixels_by_camera = [[pixel] for pixel in pixel_combo]
    
    return TriangulationResult(
        point_3d=schnittpunkt,
        mean_distance=mittlerer_abstand,
        gps_coords=(lat, lon, h),
        motion_pixels=pixels_by_camera,
        cameras_used=kameras
    )


def _remove_duplicate_results(results: List[TriangulationResult],
                              distance_threshold: float = 0.1) -> List[TriangulationResult]:
    """
    Entfernt Duplikate: Ergebnisse mit sehr aehnlichen 3D-Positionen.
    Behaelt jeweils das Ergebnis mit dem kleinsten mittleren Abstand.
    """
    if len(results) == 0:
        return []
    
    filtered = []
    
    for result in results:
        is_duplicate = False
        
        for existing in filtered:
            # Euklidischer Abstand zwischen 3D-Punkten
            distance = np.linalg.norm(result.point_3d - existing.point_3d)
            
            if distance < distance_threshold:
                is_duplicate = True
                # Falls neues Ergebnis besser ist, ersetze das alte
                if result.mean_distance < existing.mean_distance:
                    filtered.remove(existing)
                    filtered.append(result)
                break
        
        if not is_duplicate:
            filtered.append(result)
    
    return filtered