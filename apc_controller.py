import configparser
import sys
import paramiko
import time
import logging
import re

logging.basicConfig(level=logging.WARNING)


class APCController:
    def __init__(self, config_path='config.ini'):
        self.config_path = config_path
        self.host = None
        self.port = None
        self.username = None
        self.password = None
        self.client = None
        self.load_config()

    def load_config(self):
        config = configparser.ConfigParser()
        config.read(self.config_path)
        if 'ssh' not in config:
            raise ValueError("Секция [ssh] не найдена в конфигурационном файле")
        ssh = config['ssh']
        self.host = ssh['host']
        self.port = int(ssh['port'])
        self.username = ssh['username']
        self.password = ssh['password']

    def connect(self):
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=10,
                look_for_keys=False,
                allow_agent=False
            )
            print(f"✅ Подключено к {self.host}")
        except Exception as e:
            print(f"❌ Ошибка подключения: {e}")
            sys.exit(1)

    def disconnect(self):
        if self.client:
            self.client.close()
            print("🔌 SSH-соединение закрыто")

    def _run_in_shell(self, commands, timeout=10):
        """Выполняет список команд в одной сессии, возвращает список результатов."""
        try:
            shell = self.client.invoke_shell()
            shell.settimeout(timeout)

            # Ожидание приглашения
            output_buffer = ""
            start_time = time.time()
            prompt_found = False
            while time.time() - start_time < timeout:
                if shell.recv_ready():
                    chunk = shell.recv(1024).decode('utf-8', errors='ignore')
                    output_buffer += chunk
                    if 'apc>' in output_buffer.lower():
                        prompt_found = True
                        break
                time.sleep(0.2)

            if not prompt_found:
                print(f"⚠️ Не получено приглашение за {timeout} сек.")

            results = []
            for cmd in commands:
                shell.send(cmd + '\r\n')
                time.sleep(2)

                output = ""
                cmd_start = time.time()
                while time.time() - cmd_start < timeout:
                    if shell.recv_ready():
                        chunk = shell.recv(4096).decode('utf-8', errors='ignore')
                        output += chunk
                        if 'apc>' in chunk.lower():
                            break
                        time.sleep(0.1)
                    else:
                        time.sleep(0.2)

                clean_output = output.replace(cmd, "").strip()
                lines = clean_output.splitlines()
                filtered = [line for line in lines if not line.strip().endswith('apc>') and line.strip() != ""]
                results.append("\n".join(filtered).strip())

            shell.send('exit\r\n')
            time.sleep(0.5)
            shell.close()
            return results

        except Exception as e:
            print(f"❌ Ошибка в _run_in_shell: {e}")
            return [""] * len(commands)

    def _run_single_in_shell(self, cmd, timeout=10):
        """Выполняет ОДНУ команду в новой сессии (для цикличных запросов, где нужен fresh shell)."""
        try:
            shell = self.client.invoke_shell()
            shell.settimeout(timeout)

            # Ждём приглашения
            start_time = time.time()
            while time.time() - start_time < timeout:
                if shell.recv_ready():
                    chunk = shell.recv(1024).decode('utf-8', errors='ignore')
                    if 'apc>' in chunk.lower():
                        break
                time.sleep(0.2)

            shell.send(cmd + '\r\n')
            time.sleep(2)

            output = ""
            start_time = time.time()
            while time.time() - start_time < timeout:
                if shell.recv_ready():
                    output += shell.recv(4096).decode('utf-8', errors='ignore')
                    time.sleep(0.1)
                else:
                    time.sleep(0.2)

            shell.send('exit\r\n')
            time.sleep(0.3)
            shell.close()

            clean_output = output.replace(cmd, "").strip()
            lines = clean_output.splitlines()
            filtered = [line for line in lines if not line.strip().endswith('apc>') and line.strip() != ""]
            return "\n".join(filtered).strip()

        except Exception as e:
            print(f"❌ Ошибка в _run_single_in_shell: {e}")
            return ""

    def _parse_va_percent(self, status_output):
        """Извлекает 'Output VA Percent' из вывода detstatus -all."""
        match = re.search(r'Output VA Percent:\s*([0-9.]+)\s*%', status_output)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass
        return None

    def status(self):
        print("📡 Получение статуса...")
        result = self._run_in_shell(["detstatus -all"])[0]
        print(result)

    def gp1off(self):
        print("🔌 Выключение выхода 2 (GP1)...")
        result = self._run_in_shell(["ups -o 2 Off"])[0]
        print(result)

    def gp1on(self):
        print("💡 Включение выхода 2 (GP1)...")
        result = self._run_in_shell(["ups -o 2 On"])[0]
        print(result)

    def poff(self):
        print("🔍 Проверка состояния ИБП перед выключением...")
        outputs = self._run_in_shell(["ups -st", "ups -c Off"])
        state = self._parse_state(outputs[0])
        if state is None:
            print("❌ Не удалось определить состояние — выключение отменено.")
            return
        print(f"📊 Текущее состояние: {state}")
        if "Off" in state:
            print("✅ ИБП уже выключен — команда отменена.")
            return
        print("🛑 ИБП выключен.")
        if outputs[1]:
            print(outputs[1])

    def pon(self):
        print("🔍 Проверка состояния ИБП перед включением...")
        outputs = self._run_in_shell(["ups -st", "ups -c On"])
        state = self._parse_state(outputs[0])
        if state is None:
            print("❌ Не удалось определить состояние — включение отменено.")
            return
        print(f"📊 Текущее состояние: {state}")
        if "Online" in state or "On" in state:
            print("✅ ИБП уже включён — команда отменена.")
            return
        print("✅ ИБП включён.")
        if outputs[1]:
            print(outputs[1])

    def _parse_state(self, output):
        match = re.search(r'State:\s*(.+)', output)
        return match.group(1).strip() if match else None

    def poffdelay(self):
        # Загружаем параметры из конфига
        config = configparser.ConfigParser()
        config.read(self.config_path)
        if 'poffdelay' not in config:
            print("❌ Секция [poffdelay] отсутствует в config.ini")
            return

        try:
            target = float(config['poffdelay']['target_va_percent'])
            interval = int(config['poffdelay']['check_interval'])
            max_wait = int(config['poffdelay']['max_wait_time'])
        except (ValueError, KeyError) as e:
            print(f"❌ Ошибка в настройках [poffdelay]: {e}")
            return

        # 🔌 Проверка текущего состояния ИБП
        print("🔍 Проверка состояния ИБП перед ожиданием...")
        state_output = self._run_single_in_shell("ups -st")
        state = self._parse_state(state_output)
        if state is None:
            print("❌ Не удалось определить состояние ИБП — отмена операции.")
            return
        print(f"📊 Текущее состояние ИБП: {state}")

        if "Off" in state:
            print("✅ ИБП уже выключен — ожидание и выключение отменены.")
            return

        print(f"⏳ Ожидание снижения нагрузки до ≤ {target}% (проверка каждые {interval} сек, макс. {max_wait} сек)...")

        try:
            # 🔓 Открываем ОДИН shell на всю операцию
            shell = self.client.invoke_shell()
            shell.settimeout(10)

            # Ждём первое приглашение
            output_buffer = ""
            start_time = time.time()
            prompt_found = False
            while time.time() - start_time < 10:
                if shell.recv_ready():
                    chunk = shell.recv(1024).decode('utf-8', errors='ignore')
                    output_buffer += chunk
                    if 'apc>' in output_buffer.lower():
                        prompt_found = True
                        break
                time.sleep(0.2)

            if not prompt_found:
                print("⚠️ Не получено приглашение APC CLI. Продолжаем принудительно...")

            start_time = time.time()

            while True:
                # Отправляем команду статуса
                shell.send("detstatus -all\r\n")
                time.sleep(2)

                # Считываем вывод до приглашения apc>
                status_output = ""
                cmd_timeout = 10
                cmd_start = time.time()
                got_prompt = False
                while time.time() - cmd_start < cmd_timeout:
                    if shell.recv_ready():
                        chunk = shell.recv(4096).decode('utf-8', errors='ignore')
                        status_output += chunk
                        if 'apc>' in chunk.lower():
                            got_prompt = True
                            break
                        time.sleep(0.1)
                    else:
                        time.sleep(0.2)

                # Парсим VA Percent
                va_percent = self._parse_va_percent(status_output)
                if va_percent is not None:
                    print(f"📊 Текущая нагрузка (Output VA Percent): {va_percent:.1f}%")
                    if va_percent <= target:
                        print("✅ Порог достигнут. Выполняем выключение ИБП...")

                        # Отправляем команду выключения
                        shell.send("ups -c Off\r\n")
                        time.sleep(2)

                        # Считываем результат выключения
                        off_output = ""
                        cmd_start = time.time()
                        while time.time() - cmd_start < 5:
                            if shell.recv_ready():
                                off_output += shell.recv(4096).decode('utf-8', errors='ignore')
                                time.sleep(0.1)
                            else:
                                time.sleep(0.2)

                        clean_off = "\n".join(
                            line for line in off_output.splitlines()
                            if not line.strip().endswith('apc>') and 'ups -c Off' not in line
                        ).strip()
                        if clean_off:
                            print(clean_off)

                        break
                else:
                    print("⚠️ Не удалось извлечь 'Output VA Percent'")

                # Проверка таймаута
                elapsed = time.time() - start_time
                if elapsed >= max_wait:
                    print("❌ Превышено максимальное время ожидания. Отмена.")
                    break

                time.sleep(interval)

            # 🔚 Завершение сессии
            shell.send("exit\r\n")
            time.sleep(0.5)
            try:
                while shell.recv_ready():
                    shell.recv(1024)
            except:
                pass
            shell.close()

        except Exception as e:
            print(f"❌ Ошибка в poffdelay: {e}")
            try:
                shell.send("exit\r\n")
                time.sleep(0.3)
                shell.close()
            except:
                pass


def main():
    if len(sys.argv) < 2:
        print("Использование: python apc_controller.py [status|gp1off|gp1on|poff|pon|poffdelay]")
        sys.exit(1)

    action = sys.argv[1].lower()
    valid_actions = ['status', 'gp1off', 'gp1on', 'poff', 'pon', 'poffdelay']

    if action not in valid_actions:
        print(f"Неизвестная команда. Допустимые: {', '.join(valid_actions)}")
        sys.exit(1)

    controller = APCController()
    controller.connect()

    try:
        getattr(controller, action)()
    finally:
        controller.disconnect()


if __name__ == "__main__":
    main()