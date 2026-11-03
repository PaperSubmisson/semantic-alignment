public class ExException {
  int zero() {
    return 0;
  }

  static int test(int secret) {
    ExException o = null;
    if (secret > 0) {
      o = new ExException();
    } else assert false;
    int i = o.zero();
    return i;
  }
}
