import java.util.Random;

class assert6 {
  public static void func() {
    int i = new Random().nextInt();

    if (i >= 1000) if (!(i >= 1000)) assert false;
  }
}
