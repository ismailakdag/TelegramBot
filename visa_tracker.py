import sys
import json
import requests
from datetime import datetime, timezone, timedelta
import pytz
from dateutil import tz
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                           QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox,
                           QMessageBox, QSpinBox, QGroupBox, QListWidget, QListWidgetItem)
from PyQt5.QtCore import QTimer, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QIcon
import threading
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('visa_tracker.log'),
        logging.StreamHandler()
    ]
)

class AppointmentCheckerThread(QThread):
    update_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(self, settings, scan_history):
        super().__init__()
        self.settings = settings
        self.scan_history = scan_history
        self.is_running = True
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()
        self.is_running = False
        self.wait()  # Wait for the thread to finish

    def run(self):
        while self.is_running and not self._stop_event.is_set():
            try:
                self.check_appointments()
                # Sleep in small intervals to check stop_event more frequently
                for _ in range(self.settings['check_interval']):
                    if self._stop_event.is_set():
                        break
                    self.msleep(1000)  # Sleep for 1 second at a time
            except Exception as e:
                logging.error(f"Error in appointment checker thread: {str(e)}")
                self.error_signal.emit(str(e))
                if self._stop_event.is_set():
                    break

    def check_appointments(self):
        try:
            url = "https://api.schengenvisaappointments.com/api/visa-list/?format=json"
            response = requests.get(url, timeout=30)
            data = response.json()
            
            current_time = datetime.now().strftime('%d.%m.%Y - %H:%M:%S')
            
            with self._lock:
                last_check = self.scan_history.get('last_scan', 'İlk Kontrol')
            
            # Get API's last checked time and convert to Turkey time
            api_last_checked = None
            latest_update = None
            found_appointments = []
            
            if data and isinstance(data, list):
                for appointment in data:
                    if isinstance(appointment, dict) and 'last_checked' in appointment:
                        try:
                            api_time = datetime.fromisoformat(appointment['last_checked'].replace('Z', '+00:00'))
                            if latest_update is None or api_time > latest_update:
                                latest_update = api_time
                        except (ValueError, AttributeError) as e:
                            logging.warning(f"Error parsing API time: {str(e)}")
                            continue
                
                if latest_update:
                    turkey_tz = tz.tzoffset(None, 3*60*60)
                    api_time_turkey = latest_update.astimezone(turkey_tz)
                    api_last_checked = api_time_turkey.strftime('%d.%m.%Y - %H:%M:%S')

            result = {
                'current_time': current_time,
                'last_check': last_check,
                'api_last_checked': api_last_checked,
                'appointments': [],
                'error': None
            }

            for appointment in data:
                if not appointment or not isinstance(appointment, dict):
                    continue
                    
                if not all(appointment.get(field) for field in ['source_country', 'mission_country', 'appointment_date']):
                    continue
                
                if appointment['source_country'] == self.settings['source_country'] and appointment['mission_country'] == self.settings['mission_country']:
                    try:
                        apt_date = datetime.strptime(appointment['appointment_date'].split('T')[0], '%Y-%m-%d')
                        formatted_date = apt_date.strftime('%d.%m.%Y')
                        
                        appointment_info = {
                            'date': formatted_date,
                            'center': appointment.get('center_name', 'Belirtilmemiş'),
                            'visa_category': appointment.get('visa_category', 'Belirtilmemiş'),
                            'visa_subcategory': appointment.get('visa_subcategory', 'Belirtilmemiş'),
                            'people_looking': appointment.get('people_looking', 0),
                            'link': appointment.get('book_now_link', '#')
                        }
                        
                        appointment_key = f"{appointment['source_country']}_{appointment['mission_country']}_{apt_date.strftime('%Y-%m-%d')}"
                        
                        with self._lock:
                            if appointment_key in self.scan_history['appointments']:
                                prev_info = self.scan_history['appointments'][appointment_key]
                                if prev_info['people_looking'] != appointment_info['people_looking']:
                                    appointment_info['people_looking_change'] = appointment_info['people_looking'] - prev_info['people_looking']
                            
                            self.scan_history['appointments'][appointment_key] = appointment_info
                        
                        result['appointments'].append(appointment_info)
                            
                    except (ValueError, AttributeError) as e:
                        logging.error(f"Error processing appointment: {str(e)}")
                        continue
            
            with self._lock:
                self.scan_history['last_scan'] = current_time
                try:
                    with open('scan_history.json', 'w', encoding='utf-8') as f:
                        json.dump(self.scan_history, f, indent=4, ensure_ascii=False)
                except Exception as e:
                    logging.error(f"Error saving scan history: {str(e)}")
            
            self.update_signal.emit(result)
            
        except Exception as e:
            logging.error(f"Error checking appointments: {str(e)}")
            self.error_signal.emit(str(e))

class VisaTracker(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings_file = 'settings.json'
        self.scan_history_file = 'scan_history.json'
        self.load_settings()
        self.load_scan_history()
        self.checker_thread = None
        self.initUI()
        
    def closeEvent(self, event):
        self.stop_tracking()
        event.accept()

    def load_settings(self):
        default_settings = {
            'telegram_token': '',
            'telegram_chat_id': '',
            'source_country': '',
            'mission_country': '',
            'check_interval': 300,
            'scan_days': 1,
            'interval_unit': 'seconds',
            'initial_appointment_count': 5,
            'send_all_updates': True,  # New setting for message control
            'last_check': None,
            'last_appointments': {}
        }
        
        try:
            with open(self.settings_file, 'r') as f:
                saved_settings = json.load(f)
                self.settings = default_settings.copy()
                self.settings.update(saved_settings)
        except FileNotFoundError:
            self.settings = default_settings
            self.save_settings()

        # Load countries from example.json
        try:
            with open('example.json', 'r') as f:
                data = json.load(f)
                countries_set = set([item['source_country'] for item in data] + 
                                 [item['mission_country'] for item in data])
                countries_set.add('Poland')
                self.countries = sorted(list(countries_set))
        except:
            self.countries = ['Poland']

    def save_settings(self):
        with open(self.settings_file, 'w') as f:
            json.dump(self.settings, f, indent=4)

    def load_scan_history(self):
        try:
            with open(self.scan_history_file, 'r') as f:
                self.scan_history = json.load(f)
        except FileNotFoundError:
            self.scan_history = {
                'last_scan': None,
                'appointments': {}
            }
            self.save_scan_history()

    def save_scan_history(self):
        with open(self.scan_history_file, 'w') as f:
            json.dump(self.scan_history, f, indent=4)

    def initUI(self):
        self.setWindowTitle('Vize Randevu Takip Sistemi')
        self.setStyleSheet("background-color: #f0f0f0;")
        
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout()
        
        # Title
        title = QLabel('Vize Randevu Takip Sistemi')
        title_font = QFont('Arial', 16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #2c3e50; margin: 10px;")
        layout.addWidget(title)

        # Telegram Ayarları Grubu
        telegram_group = QGroupBox("Telegram Ayarları")
        telegram_group.setStyleSheet("""
            QGroupBox {
                background-color: white;
                border: 2px solid #bdc3c7;
                border-radius: 5px;
                margin-top: 10px;
                padding: 10px;
            }
            QGroupBox::title {
                color: #2c3e50;
            }
        """)
        telegram_layout = QVBoxLayout()

        # Telegram Token
        token_layout = QHBoxLayout()
        token_label = QLabel('Bot Token:')
        self.token_input = QLineEdit(self.settings['telegram_token'])
        token_layout.addWidget(token_label)
        token_layout.addWidget(self.token_input)
        telegram_layout.addLayout(token_layout)

        # Telegram Chat ID
        chat_layout = QHBoxLayout()
        chat_label = QLabel('Chat ID:')
        self.chat_input = QLineEdit(self.settings['telegram_chat_id'])
        chat_layout.addWidget(chat_label)
        chat_layout.addWidget(self.chat_input)
        telegram_layout.addLayout(chat_layout)

        telegram_group.setLayout(telegram_layout)
        layout.addWidget(telegram_group)

        # Ülke Seçimi Grubu
        country_group = QGroupBox("Ülke Seçimi")
        country_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 2px solid #bdc3c7;
                border-radius: 6px;
                margin-top: 6px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 7px;
                padding: 0px 5px 0px 5px;
            }
        """)
        country_layout = QVBoxLayout()

        # Kaynak Ülke
        source_layout = QHBoxLayout()
        source_label = QLabel('Kaynak Ülke:')
        self.source_combo = QComboBox()
        self.source_combo.addItems(self.countries)
        if self.settings['source_country'] in self.countries:
            self.source_combo.setCurrentText(self.settings['source_country'])
        source_layout.addWidget(source_label)
        source_layout.addWidget(self.source_combo)
        country_layout.addLayout(source_layout)

        # Hedef Ülke
        mission_layout = QHBoxLayout()
        mission_label = QLabel('Hedef Ülke:')
        self.mission_combo = QComboBox()
        self.mission_combo.addItems(self.countries)
        if self.settings['mission_country'] in self.countries:
            self.mission_combo.setCurrentText(self.settings['mission_country'])
        mission_layout.addWidget(mission_label)
        mission_layout.addWidget(self.mission_combo)
        country_layout.addLayout(mission_layout)

        country_group.setLayout(country_layout)
        layout.addWidget(country_group)

        # Kontrol Aralığı ve Diğer Ayarlar
        settings_group = QGroupBox("Kontrol Ayarları")
        settings_group.setStyleSheet("""
            QGroupBox {
                background-color: white;
                border: 2px solid #bdc3c7;
                border-radius: 5px;
                margin-top: 10px;
                padding: 10px;
            }
            QGroupBox::title {
                color: #2c3e50;
            }
        """)
        settings_layout = QVBoxLayout()

        # Kontrol Aralığı
        interval_layout = QHBoxLayout()
        interval_label = QLabel('Kontrol Sıklığı:')
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 3600)
        
        # Birim seçimi
        self.interval_unit_combo = QComboBox()
        self.interval_unit_combo.addItems(['Saniye', 'Dakika'])
        if self.settings['interval_unit'] == 'minutes':
            self.interval_unit_combo.setCurrentText('Dakika')
            self.interval_spin.setValue(self.settings['check_interval'] // 60)
        else:
            self.interval_unit_combo.setCurrentText('Saniye')
            self.interval_spin.setValue(self.settings['check_interval'])

        interval_layout.addWidget(interval_label)
        interval_layout.addWidget(self.interval_spin)
        interval_layout.addWidget(self.interval_unit_combo)
        settings_layout.addLayout(interval_layout)

        # Başlangıç randevu sayısı
        initial_count_layout = QHBoxLayout()
        initial_count_label = QLabel('Başlangıçta Gösterilecek Randevu Sayısı:')
        self.initial_count_spin = QSpinBox()
        self.initial_count_spin.setRange(1, 50)
        self.initial_count_spin.setValue(self.settings['initial_appointment_count'])
        initial_count_layout.addWidget(initial_count_label)
        initial_count_layout.addWidget(self.initial_count_spin)
        settings_layout.addLayout(initial_count_layout)

        # Geçmiş Randevu Tarama
        scan_layout = QHBoxLayout()
        scan_label = QLabel('Geçmiş Randevu Tarama (gün):')
        self.scan_days_spin = QSpinBox()
        self.scan_days_spin.setRange(0, 30)
        self.scan_days_spin.setValue(self.settings['scan_days'])
        self.scan_days_spin.setToolTip('0: Sadece yeni randevuları tara\n1-30: Belirtilen gün sayısı kadar geçmiş randevuları da tara')
        scan_layout.addWidget(scan_label)
        scan_layout.addWidget(self.scan_days_spin)
        settings_layout.addLayout(scan_layout)

        settings_group.setLayout(settings_layout)
        layout.addWidget(settings_group)

        # Mesaj Ayarları
        message_group = QGroupBox("Mesaj Ayarları")
        message_group.setStyleSheet("""
            QGroupBox {
                background-color: white;
                border: 2px solid #bdc3c7;
                border-radius: 5px;
                margin-top: 10px;
                padding: 10px;
            }
            QGroupBox::title {
                color: #2c3e50;
            }
        """)
        message_layout = QVBoxLayout()

        # Message frequency control
        self.update_type_combo = QComboBox()
        self.update_type_combo.addItems(['Sadece Yeni Randevuları Gönder', 'Her Kontrolde Durum Mesajı Gönder'])
        self.update_type_combo.setCurrentIndex(1 if self.settings.get('send_all_updates', True) else 0)
        message_layout.addWidget(QLabel('Mesaj Gönderme Sıklığı:'))
        message_layout.addWidget(self.update_type_combo)

        message_group.setLayout(message_layout)
        layout.addWidget(message_group)

        # Save Settings Button
        save_button = QPushButton('Ayarları Kaydet')
        save_button.setStyleSheet("""
            QPushButton {
                background-color: #2980b9;
                color: white;
                padding: 8px 15px;
                border: none;
                border-radius: 5px;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #3498db;
            }
        """)
        save_button.clicked.connect(self.save_current_settings)
        layout.addWidget(save_button)

        # Butonlar
        button_layout = QHBoxLayout()
        
        self.start_button = QPushButton('Takibi Başlat')
        self.start_button.setStyleSheet("""
            QPushButton {
                background-color: #27ae60;
                color: white;
                padding: 8px 15px;
                border: none;
                border-radius: 5px;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #2ecc71;
            }
        """)
        self.start_button.clicked.connect(self.start_tracking)
        
        self.stop_button = QPushButton('Takibi Durdur')
        self.stop_button.setStyleSheet("""
            QPushButton {
                background-color: #c0392b;
                color: white;
                padding: 8px 15px;
                border: none;
                border-radius: 5px;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #e74c3c;
            }
        """)
        self.stop_button.clicked.connect(self.stop_tracking)
        self.stop_button.setEnabled(False)
        
        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.stop_button)
        layout.addLayout(button_layout)
        
        # Status label with both current and last check times
        self.status_label = QLabel("Durum: Hazır")
        self.status_label.setStyleSheet("""
            QLabel {
                color: #2ecc71;
                margin: 10px;
                padding: 5px;
                border: 1px solid #bdc3c7;
                border-radius: 4px;
                background-color: #ffffff;
            }
        """)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # Last Message Time Label
        self.last_message_label = QLabel('Son Mesaj: -')
        self.last_message_label.setStyleSheet("color: #2c3e50; margin: 10px;")
        layout.addWidget(self.last_message_label)
        
        main_widget.setLayout(layout)
        self.setMinimumSize(500, 600)

    def save_current_settings(self):
        self.settings['telegram_token'] = self.token_input.text().strip()
        self.settings['telegram_chat_id'] = self.chat_input.text().strip()
        self.settings['source_country'] = self.source_combo.currentText()
        self.settings['mission_country'] = self.mission_combo.currentText()
        self.settings['initial_appointment_count'] = self.initial_count_spin.value()
        self.settings['send_all_updates'] = self.update_type_combo.currentIndex() == 1
        
        # Convert interval to seconds for storage
        interval = self.interval_spin.value()
        if self.interval_unit_combo.currentText() == 'Dakika':
            self.settings['check_interval'] = interval * 60
            self.settings['interval_unit'] = 'minutes'
        else:
            self.settings['check_interval'] = interval
            self.settings['interval_unit'] = 'seconds'
            
        self.settings['scan_days'] = self.scan_days_spin.value()
        self.save_settings()
        QMessageBox.information(self, 'Bilgi', 'Ayarlar başarıyla kaydedildi!')

    def start_tracking(self):
        try:
            if not self.checker_thread or not self.checker_thread.isRunning():
                self.checker_thread = AppointmentCheckerThread(self.settings, self.scan_history)
                self.checker_thread.update_signal.connect(self.handle_update)
                self.checker_thread.error_signal.connect(self.handle_error)
                self.checker_thread.start()
                
                self.start_button.setEnabled(False)
                self.stop_button.setEnabled(True)
                self.status_label.setText("Durum: Aktif")
                self.status_label.setStyleSheet("color: #2ecc71; margin: 10px;")
                
                # Disable settings while tracking
                self.source_combo.setEnabled(False)
                self.mission_combo.setEnabled(False)
                self.interval_spin.setEnabled(False)
                self.interval_unit_combo.setEnabled(False)
                
                logging.info("Tracking started")
        except Exception as e:
            logging.error(f"Error starting tracking: {str(e)}")
            self.handle_error(str(e))

    def stop_tracking(self):
        try:
            if self.checker_thread and self.checker_thread.isRunning():
                logging.info("Stopping tracking...")
                self.checker_thread.stop()
                self.checker_thread = None
                
                # Re-enable settings
                self.source_combo.setEnabled(True)
                self.mission_combo.setEnabled(True)
                self.interval_spin.setEnabled(True)
                self.interval_unit_combo.setEnabled(True)
                
                self.start_button.setEnabled(True)
                self.stop_button.setEnabled(False)
                self.status_label.setText("Durum: Durduruldu")
                self.status_label.setStyleSheet("color: #e74c3c; margin: 10px;")
                
                logging.info("Tracking stopped")
        except Exception as e:
            logging.error(f"Error stopping tracking: {str(e)}")
            self.handle_error(str(e))

    def handle_update(self, result):
        try:
            status_text = (
                f"Son Kontrol: {result['current_time']}\n"
                f"Önceki Kontrol: {result['last_check']}"
            )
            if result['api_last_checked']:
                status_text += f"\nAPI Son Güncelleme: {result['api_last_checked']}"
            
            self.status_label.setText(status_text)
            self.status_label.setStyleSheet("color: #2ecc71; margin: 10px; padding: 5px; border: 1px solid #bdc3c7; border-radius: 4px; background-color: #ffffff;")
            
            # Prepare Telegram message
            status_message = (
                f"🔄 Randevu Kontrol Raporu\n"
                f"🌍 {self.settings['source_country']} ➡️ {self.settings['mission_country']}\n"
                f"⏰ Kontrol Zamanı: {result['current_time']}\n"
                f"📤 Son Mesaj: {result['last_check']}\n"
            )
            
            if result['api_last_checked']:
                status_message += f"🔄 API Son Güncelleme: {result['api_last_checked']}\n"
            
            status_message += "\n"
            
            if result['appointments']:
                # Sort appointments by date
                result['appointments'].sort(key=lambda x: datetime.strptime(x['date'], '%d.%m.%Y'))
                
                status_message += f"📅 Mevcut Randevular ({len(result['appointments'])}):\n\n"
                for apt in result['appointments']:
                    people_info = f"👥 Bekleyen Kişi: {apt['people_looking']}"
                    if 'people_looking_change' in apt:
                        change = apt['people_looking_change']
                        people_info += f" ({'+' if change > 0 else ''}{change} değişim)"
                        
                    status_message += (
                        f"📅 Tarih: {apt['date']}\n"
                        f"🏢 Merkez: {apt['center']}\n"
                        f"📋 Vize Tipi: {apt['visa_category']}\n"
                        f"📝 Alt Kategori: {apt['visa_subcategory']}\n"
                        f"{people_info}\n"
                        f"🔗 <a href='{apt['link']}'>Randevu Linki</a>\n\n"
                    )
                
                self.send_telegram_message(status_message)
            else:
                status_message += "❌ Şu anda uygun randevu bulunmamaktadır."
                if self.settings['send_all_updates']:
                    self.send_telegram_message(status_message)
                    
        except Exception as e:
            logging.error(f"Error handling update: {str(e)}")
            self.handle_error(str(e))

    def handle_error(self, error_msg):
        try:
            self.status_label.setText(f"Hata: {error_msg}")
            self.status_label.setStyleSheet("color: #c0392b; margin: 10px;")
            self.send_telegram_message(f"⚠️ Hata: {error_msg}")
            
            # Auto-restart on error if thread died
            if self.checker_thread and not self.checker_thread.isRunning():
                logging.info("Attempting to restart after error...")
                self.stop_tracking()
                self.start_tracking()
        except Exception as e:
            logging.error(f"Error in error handler: {str(e)}")

    def send_telegram_message(self, message):
        url = f"https://api.telegram.org/bot{self.settings['telegram_token']}/sendMessage"
        params = {
            'chat_id': self.settings['telegram_chat_id'],
            'text': message,
            'parse_mode': 'HTML'
        }
        try:
            response = requests.post(url, json=params)
            response.raise_for_status()
            current_time = datetime.now().strftime('%H:%M')
            self.last_message_label.setText(f'Son Mesaj: {current_time}')
        except Exception as e:
            print(f"Telegram mesajı gönderilirken hata oluştu: {str(e)}")

def main():
    app = QApplication(sys.argv)
    ex = VisaTracker()
    ex.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
