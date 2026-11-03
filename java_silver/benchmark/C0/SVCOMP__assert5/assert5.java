import java.util.Random;

class assert5 {
  public static void func() {
    int i = new Random().nextInt();

    if (i > 1000) assert i > 1000 : "i is greater 1000"; // should hold
  }
}
