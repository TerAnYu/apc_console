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

    def _wait_for_prompt(self, shell, timeout=10):
        output_buffer = ""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if shell.recv_ready():
                chunk = shell.recv(1024).decode('utf-8', errors='ignore')
                output_buffer += chunk
                if 'apc>' in output_buffer.lower():
                    return output_buffer
            time.sleep(0.2)
        return output_buffer

    def _execute_command(self, shell, cmd, timeout=10):
        shell.send(cmd + '\r\n')
        time.sleep(1.5)

        output = ""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if shell.recv_ready():
                chunk = shell.recv(4096).decode('utf-8', errors='ignore')
                output += chunk
                if 'apc>' in chunk.lower():
                    break
            time.sleep(0.1)

        lines = output.splitlines()
        clean_lines = [
            line for line in lines
            if cmd.strip() not in line and not line.strip().endswith('apc>') and line.strip()
        ]
        return "\n".join(clean_lines).strip()

    def _run_in_shell(self, commands, timeout=10):
        try:
            shell = self.client.invoke_shell()
            shell.settimeout(timeout)
            self._wait_for_prompt(shell, timeout)

            results = []
            for cmd in commands:
                result = self._execute_command(shell, cmd, timeout)
                results.append(result)

            shell.send('exit\r\n')
            time.sleep(0.3)
            shell.close()
            return results
        except Exception as e:
            print(f"❌ Ошибка в _run_in_shell: {e}")
            return [""] * len(commands)

    def _parse_va_percent(self, status_output):
        match = re.search(r'Output VA Percent:\s*([0-9.]+)\s*%', status_output)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass
        return None

    def _parse_state(self, output):
        match = re.search(r'State:\s*(.+)', output)
        return match.group(1).strip() if match else None

    def _parse_outlet1_state(self, output):
        match = re.search(r'Outlet1 State:\s*(\w+)', output)
        if match:
            state = match.group(1).strip()
            if state in ('On', 'Off'):
                return state
        return None

    def status(self):
        print("📡 Получение статуса...")
        result = self._run_in_shell(["detstatus -all"])[0]
        print(result)

    def gp1off(self):
        print("🔍 Проверка состояния Outlet1 перед выключением...")
        try:
            shell = self.client.invoke_shell()
            shell.settimeout(10)
            self._wait_for_prompt(shell, 10)

            state_output = self._execute_command(shell, "ups -os", 10)
            outlet_state = self._parse_outlet1_state(state_output)

            if outlet_state is None:
                print("❌ Не удалось определить состояние Outlet1 — операция отменена.")
                shell.send("exit\r\n")
                time.sleep(0.3)
                shell.close()
                return

            print(f"📊 Текущее состояние Outlet1: {outlet_state}")

            if outlet_state == "Off":
                print("✅ Outlet1 уже выключен — команда отменена.")
            else:
                print("🔌 Выключение Outlet1 (выход 2)...")
                result = self._execute_command(shell, "ups -o 2 Off", 5)
                if result:
                    print(result)

            shell.send("exit\r\n")
            time.sleep(0.3)
            shell.close()

        except Exception as e:
            print(f"❌ Ошибка в gp1off: {e}")
            try:
                shell.send("exit\r\n")
                time.sleep(0.3)
                shell.close()
            except:
                pass

    def gp1on(self):
        print("🔍 Проверка состояния Outlet1 перед включением...")
        try:
            shell = self.client.invoke_shell()
            shell.settimeout(10)
            self._wait_for_prompt(shell, 10)

            state_output = self._execute_command(shell, "ups -os", 10)
            outlet_state = self._parse_outlet1_state(state_output)

            if outlet_state is None:
                print("❌ Не удалось определить состояние Outlet1 — операция отменена.")
                shell.send("exit\r\n")
                time.sleep(0.3)
                shell.close()
                return

            print(f"📊 Текущее состояние Outlet1: {outlet_state}")

            if outlet_state == "On":
                print("✅ Outlet1 уже включён — команда отменена.")
            else:
                print("💡 Включение Outlet1 (выход 2)...")
                result = self._execute_command(shell, "ups -o 2 On", 5)
                if result:
                    print(result)

            shell.send("exit\r\n")
            time.sleep(0.3)
            shell.close()

        except Exception as e:
            print(f"❌ Ошибка в gp1on: {e}")
            try:
                shell.send("exit\r\n")
                time.sleep(0.3)
                shell.close()
            except:
                pass

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

    def poffdelay(self):
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

        try:
            shell = self.client.invoke_shell()
            shell.settimeout(10)
            self._wait_for_prompt(shell, 10)

            print("🔍 Проверка состояния ИБП перед ожиданием...")
            state_output = self._execute_command(shell, "ups -st", 10)
            state = self._parse_state(state_output)
            if state is None:
                print("❌ Не удалось определить состояние ИБП — отмена операции.")
                shell.send("exit\r\n")
                time.sleep(0.3)
                shell.close()
                return

            print(f"📊 Текущее состояние ИБП: {state}")

            if "Off" in state:
                print("✅ ИБП уже выключен — ожидание и выключение отменены.")
                shell.send("exit\r\n")
                time.sleep(0.3)
                shell.close()
                return

            print(f"⏳ Ожидание снижения нагрузки до ≤ {target}% (проверка каждые {interval} сек, макс. {max_wait} сек)...")

            start_time = time.time()
            while True:
                status_output = self._execute_command(shell, "detstatus -all", 10)
                va_percent = self._parse_va_percent(status_output)

                if va_percent is not None:
                    print(f"📊 Текущая нагрузка (Output VA Percent): {va_percent:.1f}%")
                    if va_percent <= target:
                        print("✅ Порог достигнут. Выполняем выключение ИБП...")
                        off_result = self._execute_command(shell, "ups -c Off", 5)
                        if off_result:
                            print(off_result)
                        break
                else:
                    print("⚠️ Не удалось извлечь 'Output VA Percent'")

                elapsed = time.time() - start_time
                if elapsed >= max_wait:
                    print("❌ Превышено максимальное время ожидания. Отмена.")
                    break

                time.sleep(interval)

            shell.send("exit\r\n")
            time.sleep(0.5)
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
    valid_actions = {'status', 'gp1off', 'gp1on', 'poff', 'pon', 'poffdelay'}

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